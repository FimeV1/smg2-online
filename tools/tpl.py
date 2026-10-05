"""TPL textures in RGB5A3 (the format of the title logo): decode to RGBA rows and back."""
import struct, zlib

FMT_RGB5A3 = 5

def read(tpl):
    """-> (width, height, format, data offset)"""
    _magic, _count, table = struct.unpack_from(">III", tpl, 0)
    image, _palette = struct.unpack_from(">II", tpl, table)
    height, width, fmt, data = struct.unpack_from(">HHII", tpl, image)
    return width, height, fmt, data

def decode_rgb5a3(tpl):
    """-> width, height, pixels[y][x] = (r, g, b, a)"""
    width, height, fmt, off = read(tpl)
    assert fmt == FMT_RGB5A3
    px = [[(0, 0, 0, 0)] * width for _ in range(height)]
    for by in range(0, height, 4):
        for bx in range(0, width, 4):
            for y in range(4):
                for x in range(4):
                    v, = struct.unpack_from(">H", tpl, off); off += 2
                    if v & 0x8000:
                        c = (((v >> 10) & 31) * 255 // 31, ((v >> 5) & 31) * 255 // 31, (v & 31) * 255 // 31, 255)
                    else:
                        c = (((v >> 8) & 15) * 17, ((v >> 4) & 15) * 17, (v & 15) * 17, ((v >> 12) & 7) * 255 // 7)
                    if by + y < height and bx + x < width:
                        px[by + y][bx + x] = c
    return width, height, px

def encode_rgb5a3(tpl, px):
    """A copy of `tpl` with its image replaced by `px` (same size)."""
    width, height, fmt, off = read(tpl)
    out = bytearray(tpl)
    for by in range(0, height, 4):
        for bx in range(0, width, 4):
            for y in range(4):
                for x in range(4):
                    r, g, b, a = px[min(by + y, height - 1)][min(bx + x, width - 1)]
                    if a >= 240:
                        v = 0x8000 | (r * 31 // 255) << 10 | (g * 31 // 255) << 5 | (b * 31 // 255)
                    else:
                        v = (a * 7 // 255) << 12 | (r // 17) << 8 | (g // 17) << 4 | (b // 17)
                    struct.pack_into(">H", out, off, v); off += 2
    return bytes(out)

def write_png(path, width, height, px):
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(b"\0" + bytes(v for p in row for v in p) for row in px)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
