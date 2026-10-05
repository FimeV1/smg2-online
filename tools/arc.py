"""Read Nintendo .arc files (Yaz0 + RARC): py tools/arc.py <file.arc> [extract_dir]"""
import struct, sys, os

def yaz0(d):
    if d[:4] != b"Yaz0":
        return d
    size, = struct.unpack_from(">I", d, 4)
    out = bytearray(); i = 16
    while len(out) < size:
        code = d[i]; i += 1
        for bit in range(8):
            if len(out) >= size: break
            if code & (0x80 >> bit):
                out.append(d[i]); i += 1
            else:
                b1, b2 = d[i], d[i + 1]; i += 2
                dist = ((b1 & 0xF) << 8 | b2) + 1
                n = b1 >> 4
                if n == 0:
                    n = d[i] + 0x12; i += 1
                else:
                    n += 2
                for _ in range(n):
                    out.append(out[-dist])
    return bytes(out)

def rarc(d):
    """{path: bytes}"""
    assert d[:4] == b"RARC"
    hdr = struct.unpack_from(">IIIIII", d, 4)
    data_off = hdr[2] + 0x20
    ndirs, dirs_off, nfiles, files_off, strs_size, strs_off = struct.unpack_from(">IIIIII", d, 0x20)
    dirs_off += 0x20; files_off += 0x20; strs_off += 0x20
    def name(o):
        e = d.index(b"\0", strs_off + o); return d[strs_off + o:e].decode("latin-1")
    out = {}
    def walk(di, prefix):
        _t, noff, _h, cnt, first = struct.unpack_from(">4sIHHI", d, dirs_off + di * 16)
        for fi in range(first, first + cnt):
            fid, _fh, attr, noff2, off, size = struct.unpack_from(">HHHHII", d, files_off + fi * 20)
            n = name(noff2)
            if attr & 0x200:
                if n not in (".", ".."): walk(off, prefix + n + "/")
            else:
                out[prefix + n] = d[data_off + off:data_off + off + size]
    walk(0, "")
    return out

def load(path):
    return rarc(yaz0(open(path, "rb").read()))

if __name__ == "__main__":
    files = load(sys.argv[1])
    for n, b in files.items():
        print(f"{len(b):8d} {n}")
        if len(sys.argv) > 2:
            p = os.path.join(sys.argv[2], n); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "wb").write(b)
