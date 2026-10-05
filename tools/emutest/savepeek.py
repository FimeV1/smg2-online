"""Read or poke the save data of a running test game.

    py savepeek.py N                     star flags of every galaxy that has any
    py savepeek.py N give GALAXY STAR    set the "has star" bit (0-based indices)
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import drive

GAME_SYSTEM = 0x807D0DA4  # SingletonHolder<GameSystem>::sInstance (SB4E)


def rd(n, kind, addr, length=None):
    spec = {"type": kind, "addr": addr}
    if length:
        spec["len"] = length
    return drive.send(n, {"op": "read", "specs": [spec]})["values"][0]


def galaxies(n):
    system = rd(n, "u32", GAME_SYSTEM)
    director = rd(n, "u32", system + 0xC)
    save_sequence = rd(n, "u32", director + 0x8)
    user_file = rd(n, "u32", save_sequence + 0xC)
    holder = rd(n, "u32", user_file)
    storage = rd(n, "u32", holder + 0xC)
    array, count = rd(n, "u32", storage + 0x8), rd(n, "u32", storage + 0x10)
    out = []
    for i in range(count):
        galaxy = rd(n, "u32", array + 4 * i)
        scenarios, accessors = rd(n, "u32", galaxy + 0xC), rd(n, "u32", galaxy + 0x8)
        out.append((accessors, scenarios))
    return out


if __name__ == "__main__":
    n = sys.argv[1]
    table = galaxies(n)
    if len(sys.argv) > 2 and sys.argv[2] == "give":
        accessors, scenarios = table[int(sys.argv[3])]
        addr = accessors + 0xC * int(sys.argv[4]) + 8
        value = rd(n, "u8", addr) | 1
        drive.send(n, {"op": "write", "specs": [{"type": "u8", "addr": addr, "value": value}]})
        print("gave star", sys.argv[3], sys.argv[4])
    else:
        total = 0
        for i, (accessors, scenarios) in enumerate(table):
            flags = [rd(n, "u8", accessors + 0xC * s + 8) for s in range(scenarios)]
            total += sum(f & 1 for f in flags)
            if any(flags):
                print(f"galaxy {i}: {flags}")
        print("stars:", total)
