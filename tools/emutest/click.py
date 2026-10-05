"""py click.py N PX PY [wait_frames] - point at pixel (PX, PY) of an 834x456 screenshot, press A, wait,
then screenshot to click-N.png"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import drive
n = sys.argv[1]
x, y = (float(sys.argv[2]) - 422) / 547, (207 - float(sys.argv[3])) / 231
wait = int(sys.argv[4]) if len(sys.argv) > 4 else 150
p = [x, y]
steps = [{"ptr": p, "frames": 40}, {"ptr": p, "wm": {"A": True}, "frames": 8}, {"ptr": p, "frames": wait + 120}]
drive.send(n, {"op": "seq", "steps": steps})
time.sleep((48 + wait) / 60 + 0.5)
for _ in range(2):
    drive.send(n, {"op": "shot", "path": os.path.join(drive.SP, "click-%s.png" % n), "scale": 1})
    time.sleep(0.3)
