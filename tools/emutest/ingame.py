# Runs INSIDE the scripting build of Dolphin. Executes commands dropped into
# cmd-<N>.json by drive.py and answers in ack-<N>.json.
from dolphin import event, memory, controller
import json
import os
import struct
import traceback
import zlib

N = os.environ.get("SMG_TEST_ID", "1")
DIR = os.environ["SMG_TEST_DIR"]
CMD = os.path.join(DIR, "cmd-%s.json" % N)
ACK = os.path.join(DIR, "ack-%s.json" % N)


def write_png(path, width, height, rgb, scale=2):
    # nearest-neighbour downscale, then a minimal PNG encoder
    w, h = width // scale, height // scale
    rows = bytearray()
    for y in range(h):
        rows.append(0)
        start = y * scale * width * 3
        row = rgb[start:start + width * 3]
        if scale == 1:
            rows += row
        else:
            for x in range(w):
                o = x * scale * 3
                rows += row[o:o + 3]

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(rows), 6)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def read_mem(spec):
    kind, addr = spec["type"], spec["addr"]
    if kind == "u8":
        return memory.read_u8(addr)
    if kind == "u16":
        return memory.read_u16(addr)
    if kind == "u32":
        return memory.read_u32(addr)
    if kind == "f32":
        return memory.read_f32(addr)
    if kind == "bytes":
        return bytes(memory.read_u8(addr + i) for i in range(spec["len"])).hex()
    if kind == "str":
        out = bytearray()
        for i in range(spec.get("len", 64)):
            b = memory.read_u8(addr + i)
            if b == 0:
                break
            out.append(b)
        return out.decode("latin-1")
    raise ValueError(kind)


WM_OFF = {"A": False, "B": False, "One": False, "Two": False, "Plus": False, "Minus": False,
          "Home": False, "Up": False, "Down": False, "Left": False, "Right": False}
NC_OFF = {"C": False, "Z": False, "StickX": 0.0, "StickY": 0.0}

queue = []          # input steps run back to back, one per frame budget
frame = 0
last_id = None


def apply_inputs():
    while queue and queue[0]["frames"] <= 0:
        queue.pop(0)
    if not queue:
        return
    step = queue[0]
    step["frames"] -= 1
    try:
        b = dict(WM_OFF)
        b.update(step.get("wm") or {})
        controller.set_wiimote_buttons(0, b)
        n = dict(NC_OFF)
        n.update(step.get("nc") or {})
        controller.set_wii_nunchuk_buttons(0, n)
        if step.get("ptr") is not None:
            controller.set_wiimote_pointer(0, step["ptr"][0], step["ptr"][1])
        if step.get("shake"):
            controller.set_wiimote_shake(0, 1.0, 1.0, 1.0)
    except Exception:
        queue.clear()
        with open(os.path.join(DIR, "err-%s.txt" % N), "a") as f:
            f.write(traceback.format_exc())


event.on_frameadvance(apply_inputs)

while True:
    await event.frameadvance()
    frame += 1
    if frame % 4:
        continue
    try:
        with open(CMD) as f:
            cmd = json.load(f)
    except (OSError, ValueError):
        continue
    if cmd.get("id") == last_id:
        continue
    last_id = cmd.get("id")
    ack = {"id": last_id, "frame": frame}
    try:
        op = cmd["op"]
        if op == "api":
            ack["controller"] = [n for n in dir(controller) if not n.startswith("_")]
            ack["memory"] = [n for n in dir(memory) if not n.startswith("_")]
            ack["event"] = [n for n in dir(event) if not n.startswith("_")]
        elif op == "input":
            queue.clear()
            queue.append(cmd)
        elif op == "seq":
            queue.clear()
            queue.extend(cmd["steps"])
        elif op == "busy":
            ack["remaining"] = sum(s["frames"] for s in queue)
        elif op == "shot":
            width, height, data = await event.framedrawn()
            write_png(cmd["path"], width, height, data, cmd.get("scale", 2))
            ack["size"] = [width, height]
        elif op == "read":
            ack["values"] = [read_mem(s) for s in cmd["specs"]]
        elif op == "write":
            for s in cmd["specs"]:
                getattr(memory, "write_" + s["type"])(s["addr"], s["value"])
        elif op == "py":
            ack["result"] = repr(eval(cmd["expr"], {"controller": controller, "memory": memory, "queue": queue}))
        elif op == "ping":
            pass
    except Exception:
        ack["error"] = traceback.format_exc()
    # drive.py may have the ack file open for reading at this very moment, and
    # Windows then refuses the replace: try again on later frames, never die.
    tmp = ACK + ".tmp"
    for _ in range(40):
        try:
            with open(tmp, "w") as f:
                json.dump(ack, f)
            os.replace(tmp, ACK)
            break
        except OSError:
            await event.frameadvance()
            frame += 1
