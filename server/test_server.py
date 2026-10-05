#!/usr/bin/env python3
"""
Tests for the SMG2 Online server: py -3 test_server.py

FakeGame speaks the wire protocol the way client/source/savesync.cpp does, so
these tests cover the merge rules and the delivery of save progress end to end
(late joiners, lost datagrams, a server restart), without an emulator.
"""
import os
import random
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import savemerge  # noqa: E402
import smg2_server as srv  # noqa: E402

FRESH_PATH = os.path.join(HERE, "fresh_SB4E.bin")
with open(FRESH_PATH, "rb") as _f:
    FRESH = _f.read()
LAYOUT = savemerge.Layout(FRESH)
LAYOUT.set_reference(FRESH)


def field(name):
    return next(f for f in LAYOUT.fields if f.name == name)


def star_fields():
    return [f for _galaxy, stars in LAYOUT.galaxies for f in stars]


def give_star(blob, index):
    f = star_fields()[index]
    f.put(blob, f.get(blob) | savemerge.SCENARIO_FLAG_STAR)


class FakeGame:
    """One game: a save blob plus the client side of the protocol."""

    def __init__(self, port, loss=0.0, seed=0):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.setblocking(False)
        self.server = ("127.0.0.1", port)
        self.loss = loss
        self.rng = random.Random(seed)
        self.nonce = self.rng.randrange(1, 0xFFFFFFFF)
        self.save = bytearray(FRESH)   # what the game holds, private fields included
        self.connected = False
        self.id = None
        self.local_ranges = []
        self.poses = {}
        self.stage = (0, 0)
        self.reset()

    def reset(self):
        self.acked = None
        self.acked_id = 0
        self.report = None
        self.report_seq = getattr(self, "report_seq", 0)
        self.report_full = False
        self.report_sent = 0.0
        self.staging = None
        self.staging_key = None
        self.applied_seq = 0
        self.updates_applied = getattr(self, "updates_applied", 0)

    # ---- transport ----
    def send(self, records):
        if self.rng.random() < self.loss:
            return
        self.sock.sendto(struct.pack(">I", srv.MAGIC) + b"".join(records), self.server)

    def canonical(self):
        out = bytearray(self.save)
        for offset, size in self.local_ranges:
            out[offset:offset + size] = bytes(size)
        return out

    def send_report(self):
        self.report_sent = time.monotonic()
        full = self.report_full or self.acked is None
        out = []
        for offset in range(0, len(self.report), srv.SAVE_BLOCK):
            block = self.report[offset:offset + srv.SAVE_BLOCK]
            if full or block != self.acked[offset:offset + srv.SAVE_BLOCK]:
                out.append(srv.record(srv.TAG_SAVE_BLOCK, struct.pack(">IHH", self.report_seq, offset, len(block)) + block))
        out.append(srv.record(srv.TAG_SAVE_COMMIT, struct.pack(
            ">IIIHH", self.report_seq, self.acked_id if self.acked is not None else 0,
            srv.checksum(self.report), len(self.report), 0 if self.acked is not None else srv.SAVE_FLAG_INITIAL)))
        # the real client packs these into datagrams of at most 1280 bytes
        batch, size = [], 4
        for r in out:
            if size + len(r) > srv.MAX_DATAGRAM:
                self.send(batch)
                batch, size = [], 4
            batch.append(r)
            size += len(r)
        self.send(batch)

    def step(self):
        """One frame."""
        now = time.monotonic()
        while True:
            try:
                data, _ = self.sock.recvfrom(2048)
            except (BlockingIOError, ConnectionResetError):
                break
            if self.rng.random() < self.loss:
                continue
            self.on_datagram(data)

        if not self.connected:
            self.send([srv.record(srv.TAG_HELLO, struct.pack(">II", 0x53423445, self.nonce))])
            return

        pose = struct.pack(">IB", self.stage[0], self.stage[1]) + bytes(srv.POSE_SIZE - 5)
        self.send([srv.record(srv.TAG_POSE, pose)])

        current = self.canonical()
        if self.report is None:
            if self.acked is None or current != self.acked:
                self.start_report(current)
        elif current != self.report:
            self.start_report(current)
        elif now - self.report_sent > 0.2:
            self.send_report()

    def start_report(self, current):
        self.report = current
        self.report_seq += 1
        self.report_full = False
        self.send_report()

    def on_datagram(self, data):
        offset = 4
        while offset + 4 <= len(data):
            tag, arg, size = struct.unpack_from(">BBH", data, offset)
            payload = data[offset + 4:offset + 4 + size]
            offset += 4 + size + (-size % 4)
            if tag == srv.TAG_WELCOME and not self.connected:
                epoch, self.id, _max, share, count = struct.unpack_from(">IBBBB", payload)
                self.local_ranges = [struct.unpack_from(">HH", payload, 8 + 4 * i) for i in range(count)]
                self.connected = True
                self.reset()
            elif tag == srv.TAG_PLAYER_POSE:
                self.poses[arg] = payload
            elif tag == srv.TAG_SAVE_ACK:
                seq, ok = struct.unpack_from(">II", payload)
                if self.report is not None and seq == self.report_seq:
                    if ok:
                        self.acked, self.acked_id, self.report = self.report, seq, None
                    else:
                        self.report_full = True
                        self.send_report()
            elif tag == srv.TAG_UPDATE_BLOCK:
                seq, at, size = struct.unpack_from(">IHH", payload)
                if self.acked is None:
                    continue
                if self.staging is None or self.staging_key != (seq, self.acked_id):
                    self.staging, self.staging_key = bytearray(self.acked), (seq, self.acked_id)
                self.staging[at:at + size] = payload[8:8 + size]
            elif tag == srv.TAG_UPDATE_COMMIT:
                seq, base_seq, check, total, _flags = struct.unpack_from(">IIIHH", payload)
                if seq == self.applied_seq and seq:
                    self.send([srv.record(srv.TAG_SAVE_APPLIED, struct.pack(">I", seq))])
                    continue
                if self.acked is None or self.report is not None or base_seq != self.acked_id:
                    continue
                if self.staging is None or self.staging_key != (seq, self.acked_id):
                    continue
                if srv.checksum(self.staging) != check or self.canonical() != self.acked:
                    continue
                loaded = bytearray(self.staging)
                for at, size in self.local_ranges:
                    loaded[at:at + size] = self.save[at:at + size]
                self.save = loaded
                self.acked, self.acked_id = self.staging, seq | srv.UPDATE_ID_FLAG
                self.applied_seq, self.staging = seq, None
                self.updates_applied += 1
                self.send([srv.record(srv.TAG_SAVE_APPLIED, struct.pack(">I", seq))])

    def close(self):
        self.sock.close()


class ServerTest(unittest.TestCase):
    merge_on_join = True

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.dir.name, "world.bin")
        self.games = []
        self.start_server()

    def start_server(self, port=0):
        world = srv.World(self.state, FRESH_PATH)
        self.server = srv.Server("127.0.0.1", port, 32, world, merge_on_join=self.merge_on_join)
        self.port = self.server.sock.getsockname()[1]
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()

    def stop_server(self):
        self.server.running = False
        self.thread.join(2)
        self.server.close()

    def tearDown(self):
        self.stop_server()
        for g in self.games:
            g.close()
        self.dir.cleanup()

    def game(self, **kw):
        g = FakeGame(self.port, seed=len(self.games) + 1, **kw)
        self.games.append(g)
        return g

    def run_until(self, condition, timeout=8.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for g in self.games:
                g.step()
            if condition():
                return
            time.sleep(0.005)
        self.fail("timed out waiting for the games to settle")

    def settled(self):
        """Every game holds the world's progress and nothing is in flight."""
        master = bytes(self.server.world.master)
        return all(g.connected and g.report is None and g.acked is not None and bytes(g.canonical()) == master
                   for g in self.games)

    def settle(self, timeout=8.0):
        self.run_until(self.settled, timeout)


class SaveSyncTest(ServerTest):
    def test_star_reaches_everyone(self):
        a, b, c = self.game(), self.game(), self.game()
        self.settle()
        give_star(a.save, 0)
        self.settle()
        for g in (a, b, c):
            self.assertEqual(LAYOUT.count_stars(g.save), 1)

    def test_fresh_file_receives_the_world(self):
        a = self.game()
        for i in range(40):
            give_star(a.save, i)
        self.settle()
        late = self.game()   # a new file joining a world that is already under way
        self.settle()
        self.assertEqual(LAYOUT.count_stars(late.save), 40)
        self.assertEqual(bytes(late.canonical()), bytes(a.canonical()))

    def test_full_file(self):
        """A 100% file: every star, every flag, every Hungry Luma, every value."""
        a = self.game()
        for f in LAYOUT.fields:
            if f.kind == "bits":
                f.put(a.save, (1 << (8 * f.parts[0][1])) - 1 if not f.name.startswith("flag ") else f.get(a.save) | 0x80)
            elif f.kind == "max":
                f.put(a.save, 2 if "acb4" in f.name else 3)
            elif f.kind == "add":
                f.put(a.save, 1234)
            elif f.kind in ("lww", "min"):
                f.put(a.save, 77)
        b = self.game()
        self.settle()
        self.assertEqual(LAYOUT.count_stars(b.save), 242)
        self.assertEqual(bytes(b.canonical()), bytes(a.canonical()))
        shared = [f for f in LAYOUT.fields if f.kind != "local"]
        self.assertTrue(all(f.get(b.save) == f.get(a.save) for f in shared))

    def test_both_collect_at_once(self):
        a, b = self.game(), self.game()
        self.settle()
        give_star(a.save, 3)
        give_star(b.save, 9)
        self.settle()
        self.assertEqual(LAYOUT.count_stars(a.save), 2)
        self.assertEqual(LAYOUT.count_stars(b.save), 2)

    def test_private_fields_stay_private(self):
        a, b = self.game(), self.game()
        self.settle()
        lives = field("lives")
        lives.put(a.save, 9)
        lives.put(b.save, 2)
        give_star(a.save, 0)
        self.settle()
        self.assertEqual(lives.get(a.save), 9)
        self.assertEqual(lives.get(b.save), 2)
        self.assertEqual(lives.get(self.server.world.master), 0)

    def test_star_bits_add_up(self):
        a, b = self.game(), self.game()
        self.settle()
        bits = field("star bits")
        bits.put(a.save, bits.get(a.save) + 50)
        bits.put(b.save, bits.get(b.save) + 30)
        self.settle()
        self.assertEqual(bits.get(a.save), 80)
        self.assertEqual(bits.get(b.save), 80)
        bits.put(a.save, 80 - 60)   # a Hungry Luma is fed
        self.settle()
        self.assertEqual(bits.get(b.save), 20)

    def test_flag_can_turn_off(self):
        a, b = self.game(), self.game()
        flag = next(f for f in LAYOUT.fields if f.name.startswith("flag "))
        flag.put(a.save, flag.get(a.save) | 0x80)
        self.settle()
        self.assertTrue(flag.get(b.save) & 0x80)
        flag.put(b.save, flag.get(b.save) & 0x7F)
        self.settle()
        self.assertFalse(flag.get(a.save) & 0x80)

    def test_best_time_keeps_the_fastest(self):
        a, b = self.game(), self.game()
        self.settle()
        best = next(f for f in LAYOUT.fields if f.kind == "min" and "star 1" in f.name)
        best.put(a.save, 5000)
        self.settle()
        best.put(b.save, 4000)
        self.settle()
        self.assertEqual(best.get(a.save), 4000)
        best.put(a.save, 4500)   # slower: the world keeps 4000 and hands it back
        self.settle()
        self.assertEqual(best.get(a.save), 4000)

    def test_lossy_network(self):
        a, b = self.game(loss=0.3), self.game(loss=0.3)
        self.settle(30)
        for i in range(30):
            give_star(a.save if i % 2 else b.save, i)
            for _ in range(3):
                for g in self.games:
                    g.step()
        self.settle(60)
        self.assertEqual(LAYOUT.count_stars(a.save), 30)
        self.assertEqual(LAYOUT.count_stars(b.save), 30)

    def test_world_survives_a_restart(self):
        a = self.game()
        give_star(a.save, 5)
        self.settle()
        self.stop_server()
        self.start_server(self.port)
        a.connected = False        # the game notices the silence and says HELLO again
        late = self.game()
        self.settle()
        self.assertEqual(LAYOUT.count_stars(late.save), 1)

    def test_garbage_is_ignored(self):
        a = self.game()
        self.settle()
        junk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rng = random.Random(7)
        for _ in range(300):
            data = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 200)))
            junk.sendto(data, ("127.0.0.1", self.port))
            junk.sendto(struct.pack(">I", srv.MAGIC) + data, ("127.0.0.1", self.port))
        junk.close()
        give_star(a.save, 1)
        self.settle()
        self.assertEqual(self.server.world.stars(), 1)
        self.assertTrue(LAYOUT.matches(self.server.world.master))


class NoMergeOnJoinTest(ServerTest):
    merge_on_join = False

    def test_joiner_takes_the_world(self):
        a = self.game()
        for i in range(10):
            give_star(a.save, i)
        self.settle()
        # everything `a` brought was ignored: it now holds the (fresh) world
        self.assertEqual(LAYOUT.count_stars(a.save), 0)
        give_star(a.save, 0)   # ... but what is earned from here on counts
        self.settle()
        self.assertEqual(self.server.world.stars(), 1)


class PoseTest(ServerTest):
    def test_only_players_in_the_same_stage_see_each_other(self):
        a, b, c = self.game(), self.game(), self.game()
        a.stage, b.stage, c.stage = (111, 1), (111, 1), (222, 1)
        self.run_until(lambda: b.id in a.poses and a.id in b.poses)
        for _ in range(30):
            for g in self.games:
                g.step()
            time.sleep(0.005)
        self.assertNotIn(c.id, a.poses)
        self.assertEqual(c.poses, {})

    def test_slot_is_freed_after_silence(self):
        self.server.session_timeout = 0.3
        a = self.game()
        self.run_until(lambda: a.connected)
        self.games.remove(a)
        a.close()
        end = time.monotonic() + 3
        while self.server.by_id and time.monotonic() < end:
            time.sleep(0.05)
        self.assertEqual(self.server.by_id, {})


class LayoutTest(unittest.TestCase):
    def test_layout(self):
        self.assertEqual(sum(len(stars) for _g, stars in LAYOUT.galaxies), 242)
        self.assertEqual(len(LAYOUT.galaxies), 49)
        self.assertTrue(LAYOUT.matches(FRESH))
        self.assertFalse(LAYOUT.matches(FRESH[:-1]))
        broken = bytearray(FRESH)
        broken[5] ^= 0xFF   # chunk signature
        self.assertFalse(LAYOUT.matches(broken))

    def test_wide_values(self):
        glider = field("グライダー[ジャングル]")
        self.assertEqual(glider.get(FRESH), 5400)
        blob = bytearray(FRESH)
        glider.put(blob, 0x12345)
        self.assertEqual(glider.get(blob), 0x12345)


if __name__ == "__main__":
    unittest.main(verbosity=1)
