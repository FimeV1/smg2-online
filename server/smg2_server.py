#!/usr/bin/env python3
"""
SMG2 Online - server

Runs anywhere Python 3.8+ runs (Windows, macOS, Linux). No dependencies.

    python smg2_server.py                # listen on UDP 5030
    python smg2_server.py --help

What it does
  * Hands out player ids and frees them when a game goes quiet.
  * Relays each player's pose to the players in the same galaxy and star.
  * Keeps the shared world: one merged save file. Every game reports its
    save when it changes; the server merges the change in (savemerge.py) and
    sends the result to everyone, reliably, including late joiners.

Wire format: see client/include/protocol.h.
"""
import argparse
import os
import random
import select
import socket
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import savemerge  # noqa: E402

MAGIC = 0x53324F01
VERSION = "1.0"

TAG_HELLO, TAG_POSE, TAG_SAVE_BLOCK, TAG_SAVE_COMMIT, TAG_SAVE_APPLIED, TAG_KEEPALIVE = 1, 2, 3, 4, 5, 6
TAG_WELCOME, TAG_PLAYER_POSE, TAG_SAVE_ACK, TAG_UPDATE_BLOCK, TAG_UPDATE_COMMIT, TAG_SERVER_KEEPALIVE = 16, 17, 18, 19, 20, 21

GAME_IDS = {0x53423445: "SB4E"}
POSE_SIZE = 68
SAVE_BLOCK = 256
SAVE_FLAG_INITIAL = 1
UPDATE_ID_FLAG = 0x80000000

MAX_DATAGRAM = 1280
TICK_HZ = 30
POSE_MAX_AGE = 1.0            # stop relaying a pose that has not been refreshed
SESSION_TIMEOUT = 30.0        # free a slot after this much silence
KEEPALIVE_INTERVAL = 1.0
UPDATE_RESEND = 0.5           # seconds before an unanswered save update is sent again
STATE_SAVE_INTERVAL = 2.0


def checksum(data):
    value = 0x811C9DC5
    for b in data:
        value = ((value ^ b) * 0x01000193) & 0xFFFFFFFF
    return value


def record(tag, payload=b"", arg=0):
    pad = -len(payload) % 4
    return struct.pack(">BBH", tag, arg, len(payload)) + payload + bytes(pad)


class Session:
    def __init__(self, addr, pid, nonce, now):
        self.addr = addr
        self.id = pid
        self.nonce = nonce
        self.last_rx = now
        self.last_tx = 0.0
        self.last_keepalive = 0.0
        self.stage = None            # (stage hash, scenario) or None
        self.pose = None
        self.pose_version = 0
        self.pose_time = 0.0
        self.sent_versions = {}
        # save sync
        self.base = None             # the blob this game holds, as far as we know
        self.base_id = 0
        self.staging = None
        self.staging_seq = None
        self.acked_report = None
        self.update = None           # blob being delivered
        self.update_seq = 0
        self.update_sent = 0.0
        self.bad_save = False


class World:
    """The shared save file and its place on disk."""

    def __init__(self, path, fresh_path):
        self.path = path
        with open(fresh_path, "rb") as f:
            fresh = f.read()
        self.layout = savemerge.Layout(fresh)
        self.layout.set_reference(fresh)
        self.master = bytearray(fresh)
        self.epoch = random.randrange(1, 0xFFFFFFFF)
        self.dirty = False
        self.last_save = 0.0
        self.load()

    def load(self):
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, "rb") as f:
                data = f.read()
            epoch, = struct.unpack_from(">I", data, 4)
            blob = data[8:]
            if data[:4] != b"S2OW" or not self.layout.matches(blob):
                raise ValueError("not a world file for this game version")
            self.master = self.layout.canonical(blob)
            self.epoch = epoch
        except (OSError, ValueError, struct.error) as err:
            backup = self.path + ".corrupt"
            print(f"[warn] could not read {self.path} ({err}); starting a fresh world, old file kept as {backup}")
            try:
                os.replace(self.path, backup)
            except OSError:
                pass

    def save(self, now, force=False):
        if not self.path or not self.dirty or (not force and now - self.last_save < STATE_SAVE_INTERVAL):
            return
        self.last_save = now
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(b"S2OW" + struct.pack(">I", self.epoch) + bytes(self.master))
            os.replace(tmp, self.path)
            self.dirty = False
        except OSError as err:
            print(f"[warn] could not save the world to {self.path}: {err}")

    def stars(self):
        return self.layout.count_stars(self.master)


class Server:
    def __init__(self, host, port, max_players, world, verbose=False, session_timeout=SESSION_TIMEOUT,
                 share_progress=True, merge_on_join=True, update_rate=TICK_HZ):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
        except OSError:
            pass
        self.sock.bind((host, port))
        self.sock.setblocking(False)
        self.max_players = max_players
        self.world = world
        self.verbose = verbose
        self.session_timeout = session_timeout
        self.share_progress = share_progress
        self.merge_on_join = merge_on_join
        self.update_rate = update_rate
        self.by_addr = {}
        self.by_id = {}
        self.warned = 0.0
        self.running = True

    # ---- helpers -------------------------------------------------------
    def send(self, s, records):
        """Send records to a session, split over as many datagrams as needed."""
        out, size = [], 4
        for r in records:
            if size + len(r) > MAX_DATAGRAM and out:
                self._sendto(s.addr, b"".join(out))
                out, size = [], 4
            out.append(r)
            size += len(r)
        if out:
            self._sendto(s.addr, b"".join(out))
            s.last_tx = time.monotonic()

    def _sendto(self, addr, body):
        try:
            self.sock.sendto(struct.pack(">I", MAGIC) + body, addr)
        except (BlockingIOError, ConnectionResetError):
            pass  # dropped datagram; every stream here tolerates loss
        except OSError as err:
            if self.verbose:
                print(f"[warn] send to {addr} failed: {err}")

    def warn(self, now, text):
        if now - self.warned > 5.0:
            self.warned = now
            print(f"[warn] {text}")

    def drop(self, s, why):
        self.by_addr.pop(s.addr, None)
        self.by_id.pop(s.id, None)
        print(f"[-] player {s.id + 1} left ({why}); {len(self.by_id)} online")

    def welcome(self, s):
        ranges = self.world.layout.local_ranges if self.share_progress else []
        payload = struct.pack(">IBBBB", self.world.epoch, s.id, min(self.max_players, 255),
                              1 if self.share_progress else 0, len(ranges))
        payload += b"".join(struct.pack(">HH", o, n) for o, n in ranges)
        return record(TAG_WELCOME, payload)

    # ---- inbound -------------------------------------------------------
    def on_datagram(self, data, addr, now):
        if len(data) < 4 or struct.unpack_from(">I", data, 0)[0] != MAGIC:
            if len(data) >= 4 and data[:3] == b"S2O":
                self.warn(now, f"{addr[0]} runs a different version of the mod than this server ({VERSION})")
            return
        s = self.by_addr.get(addr)
        offset = 4
        while offset + 4 <= len(data):
            tag, arg, size = struct.unpack_from(">BBH", data, offset)
            payload = data[offset + 4: offset + 4 + size]
            offset += 4 + size + (-size % 4)
            if len(payload) < size:
                break
            if tag == TAG_HELLO:
                s = self.on_hello(payload, addr, now, s)
            elif s is not None:
                s.last_rx = now
                self.on_record(s, tag, payload, now)
            # records from unknown addresses are ignored: that game notices the
            # silence and says HELLO again on its own

    def on_hello(self, payload, addr, now, s):
        if len(payload) < 8:
            return s
        game_id, nonce = struct.unpack_from(">II", payload)
        if game_id not in GAME_IDS:
            self.warn(now, f"{addr[0]} runs an unsupported game version ({game_id:08X})")
            return s
        if s is not None and s.nonce != nonce:
            # same address, but the game was restarted
            self.drop(s, "restarted")
            s = None
        if s is None:
            free = next((i for i in range(self.max_players) if i not in self.by_id), None)
            if free is None:
                self.warn(now, f"server full ({self.max_players}), rejected {addr[0]}:{addr[1]}")
                return None
            s = Session(addr, free, nonce, now)
            self.by_addr[addr] = s
            self.by_id[free] = s
            print(f"[+] player {s.id + 1} joined from {addr[0]}:{addr[1]}; {len(self.by_id)} online")
        s.last_rx = now
        self.send(s, [self.welcome(s)])
        return s

    def on_record(self, s, tag, payload, now):
        if tag == TAG_POSE:
            if len(payload) < POSE_SIZE:
                return
            stage_hash, scenario = struct.unpack_from(">IB", payload)
            stage = (stage_hash, scenario) if stage_hash else None
            if stage != s.stage:
                s.stage = stage
                if self.verbose:
                    print(f"[i] player {s.id + 1} is now in stage {stage}")
            s.pose = record(TAG_PLAYER_POSE, payload[:POSE_SIZE], s.id)
            s.pose_version += 1
            s.pose_time = now
        elif not self.share_progress:
            return
        elif tag == TAG_SAVE_BLOCK:
            self.on_save_block(s, payload)
        elif tag == TAG_SAVE_COMMIT:
            self.on_save_commit(s, payload, now)
        elif tag == TAG_SAVE_APPLIED:
            if len(payload) >= 4:
                seq, = struct.unpack_from(">I", payload)
                if s.update is not None and seq == s.update_seq:
                    s.base, s.base_id, s.update = s.update, seq | UPDATE_ID_FLAG, None

    # ---- save sync -----------------------------------------------------
    def on_save_block(self, s, payload):
        if len(payload) < 8:
            return
        seq, offset, size = struct.unpack_from(">IHH", payload)
        data = payload[8:8 + size]
        blob_size = self.world.layout.size
        if len(data) != size or offset + size > blob_size:
            return
        if s.staging is None or s.staging_seq != seq:
            s.staging = bytearray(s.base) if s.base is not None else bytearray(blob_size)
            s.staging_seq = seq
        s.staging[offset:offset + size] = data

    def on_save_commit(self, s, payload, now):
        if len(payload) < 16:
            return
        seq, base_seq, check, total, flags = struct.unpack_from(">IIIHH", payload)
        if seq == s.acked_report:
            self.send(s, [record(TAG_SAVE_ACK, struct.pack(">II", seq, 1))])
            return

        layout = self.world.layout
        if flags & SAVE_FLAG_INITIAL:
            if s.base is not None:
                # that game started over (new session or another save file)
                s.base, s.update = None, None
        elif s.base is None:
            return  # we lost track of this game; it will say HELLO again and start over
        elif base_seq != s.base_id:
            if s.update is not None and base_seq == (s.update_seq | UPDATE_ID_FLAG):
                # it applied our update; the answer got lost
                s.base, s.base_id, s.update = s.update, base_seq, None
            # a commit that arrived without its blocks was staged on the wrong
            # base; the checksum below catches that

        staging = s.staging if s.staging is not None and s.staging_seq == seq else None
        if staging is None and s.base is not None:
            staging = bytearray(s.base)  # a report whose blocks all got lost
        if staging is None or total != layout.size or checksum(staging) != check:
            s.staging = None
            self.send(s, [record(TAG_SAVE_ACK, struct.pack(">II", seq, 0))])
            return
        s.staging = None

        if not layout.matches(staging):
            if not s.bad_save:
                s.bad_save = True
                print(f"[warn] player {s.id + 1} sent save data this server does not understand; "
                      f"their progress is not shared")
            s.acked_report = seq
            self.send(s, [record(TAG_SAVE_ACK, struct.pack(">II", seq, 1))])
            return

        new = layout.canonical(staging)
        before = self.world.stars()
        if s.base is None:
            changed = layout.merge_union(self.world.master, new) if self.merge_on_join else []
        else:
            changed = layout.merge(self.world.master, s.base, new)
        s.base, s.base_id, s.acked_report, s.update = new, seq, seq, None
        self.send(s, [record(TAG_SAVE_ACK, struct.pack(">II", seq, 1))])

        if changed:
            self.world.dirty = True
            stars = self.world.stars()
            names = ", ".join(sorted({f.name for f in changed})[:4]) + (", ..." if len(changed) > 4 else "")
            if stars != before:
                print(f"[*] player {s.id + 1}: the world now has {stars} Power Stars")
            elif self.verbose:
                print(f"[i] player {s.id + 1} changed {len(changed)} fields ({names})")

    def deliver_update(self, s, now):
        if s.base is None or s.bad_save:
            return
        master = self.world.master
        if s.update is not None and s.update != master:
            s.update = None  # the world moved on; send the newer one
        if s.update is None:
            if s.base == master:
                return
            s.update = bytearray(master)
            s.update_seq = (s.update_seq + 1) & 0x7FFFFFFF or 1
            s.update_sent = 0.0
        if now - s.update_sent < UPDATE_RESEND:
            return
        s.update_sent = now

        out = []
        for offset in range(0, len(master), SAVE_BLOCK):
            block = s.update[offset:offset + SAVE_BLOCK]
            if block != s.base[offset:offset + SAVE_BLOCK]:
                out.append(record(TAG_UPDATE_BLOCK, struct.pack(">IHH", s.update_seq, offset, len(block)) + block))
        out.append(record(TAG_UPDATE_COMMIT, struct.pack(
            ">IIIHH", s.update_seq, s.base_id, checksum(s.update), len(s.update), 0)))
        self.send(s, out)

    # ---- outbound ------------------------------------------------------
    def tick(self, now):
        for s in [s for s in self.by_id.values() if now - s.last_rx > self.session_timeout]:
            self.drop(s, "timed out")

        groups = {}
        for s in self.by_id.values():
            if s.stage is not None and s.pose is not None and now - s.pose_time <= POSE_MAX_AGE:
                groups.setdefault(s.stage, []).append(s)

        for s in self.by_id.values():
            members = groups.get(s.stage) if s.stage is not None else None
            if members and len(members) > 1:
                out = []
                for o in members:
                    version = (o.nonce, o.pose_version)
                    if o is not s and s.sent_versions.get(o.id) != version:
                        s.sent_versions[o.id] = version
                        out.append(o.pose)
                if out:
                    self.send(s, out)
            if self.share_progress:
                self.deliver_update(s, now)
            if now - s.last_keepalive >= KEEPALIVE_INTERVAL:
                s.last_keepalive = now
                self.send(s, [record(TAG_SERVER_KEEPALIVE, arg=min(len(self.by_id), 255))])

        self.world.save(now)

    # ---- main loop -----------------------------------------------------
    def run(self):
        interval = 1.0 / self.update_rate
        next_tick = time.monotonic() + interval
        while self.running:
            now = time.monotonic()
            try:
                ready, _, _ = select.select([self.sock], [], [], max(0.0, next_tick - now))
            except InterruptedError:
                continue
            if ready:
                for _ in range(512):  # drain what is queued before ticking
                    try:
                        data, addr = self.sock.recvfrom(2048)
                    except BlockingIOError:
                        break
                    except ConnectionResetError:
                        continue  # Windows reports ICMP "port unreachable" here
                    except OSError:
                        break
                    try:
                        self.on_datagram(data, addr, time.monotonic())
                    except (struct.error, IndexError):
                        pass  # malformed record; ignore
            now = time.monotonic()
            if now >= next_tick:
                self.tick(now)
                next_tick += interval
                if next_tick < now:  # fell behind; do not spiral
                    next_tick = now + interval

    def close(self):
        self.world.save(time.monotonic(), force=True)
        self.sock.close()


SETTINGS_FILE = "server-settings.ini"
SETTINGS_TEMPLATE = """\
# SMG2 Online server settings. Edit, save, then restart the server.
# Lines starting with # are comments.

[server]
# UDP port the server listens on. Forward this port (UDP) on your router.
# Players join with  your-ip:port  (the :port part can be left out for 5030).
port = 5030

# How many players may be connected at once (1-250).
max_players = 32

# Name of the shared world. Each name has its own saved progress, so you can
# keep several runs side by side (e.g. default, any-percent, 242-stars).
world = default

# yes = stars, comet medals, unlocks and everything else in the save file are
#       shared by everyone.
# no  = players only see each other; everyone keeps their own progress.
share_progress = yes

# What happens to the progress a player already has when they join:
# yes = it is added to the world (nobody ever loses a star).
# no  = the world wins: the joining game is brought to the world's state.
#       Use this for races from a fresh file.
merge_on_join = yes

# Seconds of silence before a player counts as gone and their slot is freed.
player_timeout = 30

# Position updates per second sent to each player (10-30).
update_rate = 30

# yes = also print stage changes and sync details in the server window.
verbose = no
"""


def load_settings(path):
    """Read the settings file (creating it with defaults on first run)."""
    import configparser
    if not os.path.exists(path):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(SETTINGS_TEMPLATE)
            print(f"[i] created {path} - edit it to change the port and other settings")
        except OSError as err:
            print(f"[warn] could not create {path}: {err}")
    cp = configparser.ConfigParser()
    cp.read_string(SETTINGS_TEMPLATE)  # defaults
    try:
        cp.read(path, encoding="utf-8-sig")
    except configparser.Error as err:
        print(f"[warn] {path} has a mistake ({err}); using defaults for the rest")
    sec = cp["server"]
    out = {}
    for key, getter, fallback in (
        ("port", sec.getint, 5030), ("max_players", sec.getint, 32),
        ("share_progress", sec.getboolean, True), ("merge_on_join", sec.getboolean, True),
        ("player_timeout", sec.getfloat, SESSION_TIMEOUT), ("update_rate", sec.getint, TICK_HZ),
        ("verbose", sec.getboolean, False),
    ):
        try:
            out[key] = getter(key)
        except ValueError:
            print(f"[warn] {path}: '{key} = {sec.get(key)}' is not valid, using {fallback}")
            out[key] = fallback
    out["world"] = sec.get("world", "default").strip() or "default"
    return out


def world_file(folder, world):
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in world)
    return os.path.join(folder, f"world-{safe}.bin")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(
        description="SMG2 Online server. Settings come from server-settings.ini next to this file; "
                    "anything given on the command line wins.")
    ap.add_argument("--settings", default=os.path.join(here, SETTINGS_FILE), help="settings file to use")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int)
    ap.add_argument("--max-players", type=int)
    ap.add_argument("--world", help="name of the shared world (its own saved progress)")
    ap.add_argument("--state", help="exact file holding the world (overrides --world)")
    ap.add_argument("--fresh", action="store_true", help="discard this world's progress and start over")
    ap.add_argument("--session-timeout", type=float, help="seconds of silence before a player's slot is freed")
    ap.add_argument("--update-rate", type=int, help="position updates per second (10-30)")
    ap.add_argument("--no-progress", action="store_true", help="do not share save progress")
    ap.add_argument("--no-merge-on-join", action="store_true", help="joining games take the world's progress")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    cfg = load_settings(args.settings)
    port = args.port if args.port is not None else cfg["port"]
    max_players = args.max_players if args.max_players is not None else cfg["max_players"]
    world_name = args.world or cfg["world"]
    state = args.state if args.state is not None else world_file(here, world_name)
    timeout = args.session_timeout if args.session_timeout is not None else cfg["player_timeout"]
    update_rate = args.update_rate if args.update_rate is not None else cfg["update_rate"]
    share_progress = cfg["share_progress"] and not args.no_progress
    merge_on_join = cfg["merge_on_join"] and not args.no_merge_on_join
    verbose = args.verbose or cfg["verbose"]

    if not 1 <= port <= 65535:
        ap.error("port must be between 1 and 65535")
    if not 1 <= max_players <= 250:
        ap.error("max_players must be between 1 and 250")
    update_rate = max(10, min(30, update_rate))
    timeout = max(0.5, timeout)

    if args.fresh and state and os.path.exists(state):
        os.replace(state, state + ".old")
        print(f"[i] previous world moved to {state}.old")

    world = World(state if share_progress else "", os.path.join(here, "fresh_SB4E.bin"))
    try:
        server = Server(args.host, port, max_players, world, verbose, timeout,
                        share_progress, merge_on_join, update_rate)
    except OSError as err:
        print(f"[error] cannot listen on {args.host}:{port}: {err}")
        print("        Is another server already running, or the port in use? "
              "Change 'port' in the settings file.")
        return 1

    print(f"SMG2 Online server {VERSION} listening on UDP port {port}")
    print(f"  max players    : {max_players}")
    if share_progress:
        print(f"  shared progress: on, world '{world_name}' ({world.stars()} of 242 Power Stars)")
        print(f"  joining players: {'add their progress to the world' if merge_on_join else 'take the world progress'}")
    else:
        print("  shared progress: off")
    print(f"  settings file  : {args.settings}")
    print("Press Ctrl+C (or close this window) to stop.")
    sys.stdout.flush()
    try:
        server.run()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(line_buffering=True, errors="replace")
    except AttributeError:
        pass
    sys.exit(main())
