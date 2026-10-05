"""List symbols the client objects need that the symbol map lacks: py tools/undefined.py [REGION]"""
import glob, struct, sys, os
region = sys.argv[1] if len(sys.argv) > 1 else "SB4E"
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
have = set()
for path in (f"{root}/Syati/symbols/{region}.txt", f"{root}/client/symbols/{region}.txt"):
    for line in open(path, encoding="utf-8", errors="replace"):
        if "=" in line and not line.startswith("#"):
            have.add(line.rpartition("=")[0].strip())
defined, needed = set(), {}
for obj in glob.glob(f"{root}/client/obj/{region}/*.o"):
    if obj.endswith("loader.o"):
        continue
    d = open(obj, "rb").read()
    shoff, = struct.unpack_from(">I", d, 0x20)
    shentsize, shnum = struct.unpack_from(">HH", d, 0x2E)
    secs = [struct.unpack_from(">10I", d, shoff + i * shentsize) for i in range(shnum)]
    for s in secs:
        if s[1] != 2:  # SHT_SYMTAB
            continue
        stroff = secs[s[6]][4]
        for i in range(s[5] // 16):
            name, value, size, info, other, shndx = struct.unpack_from(">IIIBBH", d, s[4] + i * 16)
            n = d[stroff + name:d.index(b"\0", stroff + name)].decode("latin-1")
            if not n or info >> 4 == 0:
                continue
            if shndx == 0:
                needed.setdefault(n, os.path.basename(obj))
            else:
                defined.add(n)
for n in sorted(needed):
    if n not in have and n not in defined:
        print(f"{needed[n]}: {n}")
