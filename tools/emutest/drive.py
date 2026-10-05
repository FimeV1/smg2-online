"""Controls the scripted Dolphin test instances from outside.

  drive.py launch N            start instance N (minimized, no focus steal)
  drive.py kill
  drive.py api N
  drive.py shot N NAME
  drive.py press N BUTTONS [frames] [ptrx ptry]     e.g. press 1 A+B 10
  drive.py stick N X Y frames [BUTTONS]
  drive.py read N type:addr[:len] ...
"""
import json
import os
import subprocess
import sys
import time

SP = os.path.dirname(os.path.abspath(__file__))  # profiles, acks and screenshots live here
BASE = os.path.abspath(os.path.join(SP, "..", ".."))
# A Dolphin build with Python scripting (a Felk-style fork). Set SMG2_TEST_DOLPHIN,
# or put its path on the first line of dolphin-path.txt next to this file.
DOLPHIN = os.environ.get("SMG2_TEST_DOLPHIN", "")
if not DOLPHIN and os.path.exists(os.path.join(SP, "dolphin-path.txt")):
    with open(os.path.join(SP, "dolphin-path.txt"), encoding="utf-8-sig") as _f:
        DOLPHIN = _f.readline().strip()
GAME = os.path.join(BASE, "game", "Super Mario Galaxy 2 (USA) (En,Fr,Es).rvz")
PRESET = os.path.join(SP, "test-preset.json")
PIDS = os.path.join(SP, "pids.json")


def write_preset():
    d = {
        "type": "dolphin-game-mod-descriptor", "version": 1,
        "base-file": GAME.replace("\\", "/"), "display-name": "SMG2 Online test",
        "riivolution": {"patches": [{
            "xml": (BASE + r"\riivolution\riivo_SB4E.xml").replace("\\", "/"),
            "root": (BASE + r"\riivolution").replace("\\", "/"),
            "options": [{"section-name": "SMG2 Online", "option-id": "smg2online", "choice": 1}],
        }]},
    }
    with open(PRESET, "w") as f:
        json.dump(d, f)


def load_pids():
    try:
        return json.load(open(PIDS))
    except (OSError, ValueError):
        return {}



def launch_on_hidden_desktop(args, cwd):
    """Start a process on its own desktop so its windows never appear on (or
    take focus from) the desktop the user is working on."""
    import ctypes
    import ctypes.wintypes as wt

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR),
                    ("lpTitle", wt.LPWSTR), ("dwX", wt.DWORD), ("dwY", wt.DWORD), ("dwXSize", wt.DWORD),
                    ("dwYSize", wt.DWORD), ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD),
                    ("dwFillAttribute", wt.DWORD), ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD),
                    ("cbReserved2", wt.WORD), ("lpReserved2", ctypes.c_void_p), ("hStdInput", wt.HANDLE),
                    ("hStdOutput", wt.HANDLE), ("hStdError", wt.HANDLE)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE), ("dwProcessId", wt.DWORD),
                    ("dwThreadId", wt.DWORD)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.CreateDesktopW.restype = wt.HANDLE
    name = "smg2test-%d" % (int(time.time()) % 100000)
    GENERIC_ALL = 0x10000000
    hdesk = user32.CreateDesktopW(name, None, None, 0, GENERIC_ALL, None)
    if not hdesk:
        raise ctypes.WinError(ctypes.get_last_error())
    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(si)
    si.lpDesktop = name
    pi = PROCESS_INFORMATION()
    cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline(args))
    ok = kernel32.CreateProcessW(None, cmdline, None, None, False, 0, None, cwd, ctypes.byref(si), ctypes.byref(pi))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    kernel32.CloseHandle(pi.hThread)
    # report an early exit (e.g. could not start on the hidden desktop)
    if kernel32.WaitForSingleObject(pi.hProcess, 6000) == 0:
        code = wt.DWORD()
        kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
        print("EXITED EARLY with code 0x%08X" % code.value)
    kernel32.CloseHandle(pi.hProcess)
    # hdesk is deliberately left open in this process only until exit; the
    # desktop lives on while the launched process uses it.
    return pi.dwProcessId


_counter = int(time.time() * 1000) % 1000000000


def send(n, cmd, timeout=20):
    global _counter
    _counter += 1
    cmd["id"] = _counter
    ack_path = os.path.join(SP, "ack-%s.json" % n)
    tmp = os.path.join(SP, "cmd-%s.json.tmp" % n)
    with open(tmp, "w") as f:
        json.dump(cmd, f)
    for _ in range(50):
        try:
            os.replace(tmp, os.path.join(SP, "cmd-%s.json" % n))
            break
        except PermissionError:  # the instance is reading the old one right now
            time.sleep(0.02)
    end = time.time() + timeout
    while time.time() < end:
        try:
            ack = json.load(open(ack_path))
            if ack.get("id") == _counter:
                return ack
        except (OSError, ValueError):
            pass
        time.sleep(0.05)
    return {"error": "timeout (instance %s not answering)" % n}


def buttons(spec):
    names = {"A": "A", "B": "B", "PLUS": "Plus", "MINUS": "Minus", "HOME": "Home", "1": "One", "2": "Two",
             "UP": "Up", "DOWN": "Down", "LEFT": "Left", "RIGHT": "Right"}
    wm, nc, shake = {}, {}, False
    for b in spec.split("+"):
        b = b.strip().upper()
        if not b or b == "NONE":
            continue
        if b in ("C", "Z"):
            nc[b] = True
        elif b == "SHAKE":
            shake = True
        else:
            wm[names[b]] = True
    return wm, nc, shake


def main():
    cmd = sys.argv[1]
    if cmd == "launch":
        n = sys.argv[2]
        write_preset()
        for f in ("cmd-%s.json", "ack-%s.json", "err-%s.txt"):
            try:
                os.remove(os.path.join(SP, f % n))
            except OSError:
                pass
        user = os.path.join(SP, "profiles", "test%s" % n)
        args = [DOLPHIN, "-u", user, "--no-python-subinterpreters", "--script",
                os.path.join(SP, "ingame.py"), "-b", "-e", PRESET]
        os.environ["SMG_TEST_ID"] = n
        os.environ["SMG_TEST_DIR"] = SP
        pid = launch_on_hidden_desktop(args, os.path.dirname(DOLPHIN))
        pids = load_pids()
        pids[n] = pid
        json.dump(pids, open(PIDS, "w"))
        print("launched", n, pid)
    elif cmd == "kill":
        # kill [N]: one instance, or all of them
        pids = load_pids()
        for n in ([sys.argv[2]] if len(sys.argv) > 2 else list(pids)):
            if n in pids:
                subprocess.call(["taskkill", "/PID", str(pids.pop(n)), "/F"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        json.dump(pids, open(PIDS, "w"))
        print("killed")
    elif cmd == "api":
        print(json.dumps(send(sys.argv[2], {"op": "api"}), indent=1))
    elif cmd == "shot":
        n, name = sys.argv[2], sys.argv[3]
        path = os.path.join(SP, "%s-%s.png" % (name, n))
        print(send(n, {"op": "shot", "path": path, "scale": 1}), path)
    elif cmd == "press":
        n = sys.argv[2]
        wm, nc, shake = buttons(sys.argv[3])
        frames = int(sys.argv[4]) if len(sys.argv) > 4 else 8
        c = {"op": "input", "wm": wm, "nc": nc or None, "shake": shake, "frames": frames}
        if len(sys.argv) > 6:
            c["ptr"] = [float(sys.argv[5]), float(sys.argv[6])]
        print(send(n, c))
    elif cmd == "stick":
        n, x, y, frames = sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), int(sys.argv[5])
        wm, nc, shake = buttons(sys.argv[6]) if len(sys.argv) > 6 else ({}, {}, False)
        nc = dict(nc, StickX=x, StickY=y)
        print(send(n, {"op": "input", "wm": wm, "nc": nc, "shake": shake, "frames": frames}))
    elif cmd == "py":
        print(send(sys.argv[2], {"op": "py", "expr": sys.argv[3]}))
    elif cmd == "seq":
        # seq N "ptr=0.2;-0.3 NONE:30 A:8 NONE:60 stick=0;1 B:20 wait:60"
        # ptr=/stick= set the pointer / nunchuk stick for all following steps
        n = sys.argv[2]
        steps, ptr, stick = [], None, None
        for tok in sys.argv[3].split():
            if tok.startswith("ptr="):
                ptr = None if tok[4:] == "off" else [float(v) for v in tok[4:].split(";")]
                continue
            if tok.startswith("stick="):
                stick = None if tok[6:] == "off" else [float(v) for v in tok[6:].split(";")]
                continue
            name, _, frames = tok.partition(":")
            frames = int(frames or 8)
            if name == "wait":
                name = "NONE"
            wm, nc, shake = buttons(name)
            if stick:
                nc = dict(nc, StickX=stick[0], StickY=stick[1])
            steps.append({"wm": wm, "nc": nc, "ptr": ptr, "shake": shake, "frames": frames})
        r = send(n, {"op": "seq", "steps": steps})
        total = sum(s["frames"] for s in steps)
        time.sleep(total / 60.0 + 0.3)
        while send(n, {"op": "busy"}).get("remaining", 0) > 0:
            time.sleep(0.2)
        print("seq done", r.get("error", ""))
    elif cmd == "read":
        n = sys.argv[2]
        specs = []
        for s in sys.argv[3:]:
            parts = s.split(":")
            spec = {"type": parts[0], "addr": int(parts[1], 16)}
            if len(parts) > 2:
                spec["len"] = int(parts[2])
            specs.append(spec)
        print(json.dumps(send(n, {"op": "read", "specs": specs})))


if __name__ == "__main__":
    main()
