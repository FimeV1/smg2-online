import sys
sys.path.insert(0, __import__("os").path.dirname(__file__))
from ppcdis import Dol, GAMES
dol = Dol(GAMES[sys.argv[1]][0])
addr = int(sys.argv[2], 16); n = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x100
b = dol.read(addr, n)
i = 0
while i < len(b):
    j = i
    while j < len(b) and b[j]: j += 1
    s = b[i:j]
    if s:
        try: t = s.decode("shift-jis")
        except Exception: t = repr(s)
        print(f"{addr+i:08X} +{i:03X} {t!r}  raw={s.hex() if not s.isascii() else ''}")
    i = j + 1
