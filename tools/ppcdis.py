#!/usr/bin/env python3
"""Disassemble functions from a GameCube/Wii DOL using a Kamek-style symbol map.

    py tools/ppcdis.py smg2 control__10MarioActorFv            whole function
    py tools/ppcdis.py smg2 control__10MarioActorFv 0x100 12   12 instructions from +0x100
    py tools/ppcdis.py smg2 0x803C1BE0 0 40                    by address
    py tools/ppcdis.py smg2 --find MarioActor                  search symbol names
    py tools/ppcdis.py smg2 --xref trampleJump__10MarioActorFff  who branches to it
"""
import bisect
import struct
import sys
from pathlib import Path

from capstone import CS_ARCH_PPC, CS_MODE_32, CS_MODE_BIG_ENDIAN, Cs

ROOT = Path(__file__).resolve().parent.parent
GAMES = {
    "smg2": (ROOT / "dol/DATA/sys/main.dol", ROOT / "Syati/symbols/SB4E.txt"),
    "smg1": (ROOT / "dol/smg1/main.dol", ROOT / "dol/smg1/USA.txt"),
}


class Dol:
    def __init__(self, path):
        self.data = Path(path).read_bytes()
        offs = struct.unpack(">18I", self.data[0x00:0x48])
        addrs = struct.unpack(">18I", self.data[0x48:0x90])
        sizes = struct.unpack(">18I", self.data[0x90:0xD8])
        self.sections = [(a, o, s, i < 7) for i, (o, a, s) in enumerate(zip(offs, addrs, sizes)) if s]

    def read(self, addr, size):
        for a, o, s, _ in self.sections:
            if a <= addr < a + s:
                return self.data[o + addr - a:o + addr - a + size]
        return None

    def text(self):
        return [(a, self.data[o:o + s]) for a, o, s, is_text in self.sections if is_text]


class Symbols:
    def __init__(self, path):
        self.by_name = {}
        pairs = []
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" not in line or line.lstrip().startswith(("#", "//")):
                continue
            name, _, value = line.rpartition("=")
            try:
                addr = int(value.strip(), 16)
            except ValueError:
                continue
            self.by_name[name.strip()] = addr
            pairs.append((addr, name.strip()))
        pairs.sort()
        self.addrs = [p[0] for p in pairs]
        self.names = [p[1] for p in pairs]

    def lookup(self, addr):
        i = bisect.bisect_right(self.addrs, addr) - 1
        if i < 0:
            return None
        off = addr - self.addrs[i]
        return self.names[i] if off == 0 else f"{self.names[i]}+0x{off:X}"

    def next_after(self, addr):
        i = bisect.bisect_right(self.addrs, addr)
        return self.addrs[i] if i < len(self.addrs) else addr + 0x400


def resolve(sym, token):
    if token.lower().startswith("0x"):
        return int(token, 16)
    if token in sym.by_name:
        return sym.by_name[token]
    hits = [n for n in sym.by_name if n.startswith(token)]
    if len(hits) == 1:
        return sym.by_name[hits[0]]
    sys.exit(f"symbol not found or ambiguous: {token} ({hits[:8]})")


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    dol_path, sym_path = GAMES[sys.argv[1]]
    dol, sym = Dol(dol_path), Symbols(sym_path)
    args = sys.argv[2:]

    if args[0] == "--find":
        for name in sorted(sym.by_name, key=sym.by_name.get):
            if all(a in name for a in args[1:]):
                print(f"0x{sym.by_name[name]:08X} {name}")
        return

    if args[0] == "--xref":
        target = resolve(sym, args[1])
        for base, blob in dol.text():
            for i in range(0, len(blob) - 3, 4):
                word = struct.unpack_from(">I", blob, i)[0]
                if word >> 26 == 18 and not word & 2:
                    disp = word & 0x03FFFFFC
                    if disp & 0x02000000:
                        disp -= 0x04000000
                    if base + i + disp == target:
                        print(f"0x{base + i:08X} {'bl' if word & 1 else 'b '} from {sym.lookup(base + i)}")
        return

    start = resolve(sym, args[0])
    func_end = sym.next_after(start)
    offset = int(args[1], 0) if len(args) > 1 else 0
    count = int(args[2], 0) if len(args) > 2 else (func_end - start) // 4
    addr = start + offset
    blob = dol.read(addr, count * 4)
    if blob is None:
        sys.exit(f"0x{addr:08X} is not in the DOL")

    md = Cs(CS_ARCH_PPC, CS_MODE_32 | CS_MODE_BIG_ENDIAN)
    md.skipdata = True
    for ins in md.disasm(blob, addr):
        note = ""
        word = struct.unpack(">I", ins.bytes)[0]
        if word >> 26 == 18:
            disp = word & 0x03FFFFFC
            if disp & 0x02000000:
                disp -= 0x04000000
            note = f"  ; {sym.lookup(ins.address + disp)}"
        print(f"{ins.address:08X} +{ins.address - start:04X}  {ins.mnemonic:8s}{ins.op_str}{note}")


if __name__ == "__main__":
    main()
