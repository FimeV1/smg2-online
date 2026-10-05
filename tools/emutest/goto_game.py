"""Boot instance N (already launched) from the title screen into save file F (1-3).

    py goto_game.py N [F]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import drive  # noqa: E402

n = sys.argv[1]
slot = int(sys.argv[2]) if len(sys.argv) > 2 else 1
FILE_X = {1: -0.44, 2: 0.0, 3: 0.44}[slot]


def run(steps, extra=0.0):
    drive.send(n, {"op": "seq", "steps": steps})
    time.sleep(sum(s["frames"] for s in steps) / 60 + extra)


def wait_frame(target, timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        ack = drive.send(n, {"op": "ping"}, timeout=3)
        if ack.get("frame", 0) >= target:
            return
        time.sleep(0.5)
    sys.exit("instance %s is not running" % n)


wait_frame(1900)                                                            # boot to the title screen
run([{"wm": {"A": True, "B": True}, "frames": 10}], 4)                      # title
file_ptr, start_ptr = [FILE_X, -0.1], [0.51, -0.78]
run([{"ptr": file_ptr, "frames": 60}, {"ptr": file_ptr, "wm": {"A": True}, "frames": 8},
     {"ptr": file_ptr, "frames": 120}], 0.5)                                 # pick the file
run([{"ptr": start_ptr, "frames": 40}, {"ptr": start_ptr, "wm": {"A": True}, "frames": 8},
     {"ptr": start_ptr, "frames": 30}], 1)                                   # Start
wait_frame(drive.send(n, {"op": "ping"}).get("frame", 0) + 600)
print("in game", n)
