#!/usr/bin/env python3
"""Build the three download packs from a finished build: py tools/make_zips.py [output folder]

    SMG2 Online - Windows.zip        .bat launchers + mod + server
    SMG2 Online - Mac.zip            .command launchers + mod + server
    SMG2 Online - Wii and Wii U.zip  SD-card folders for Riivolution

Run client/build.sh first. The game itself is never part of a pack.
"""
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(ROOT, "dist")

COMMON = ["README.md", "LICENSE", "riivolution/CustomCode_SB4E.bin", "riivolution/riivo_SB4E.xml",
          "server/smg2_server.py", "server/savemerge.py", "server/test_server.py", "server/fresh_SB4E.bin"]
PACKS = {
    "SMG2 Online - Windows": ["HOST A GAME.bat", "JOIN A FRIEND.bat", "START SMG2 ONLINE.bat", "SERVER SETTINGS.bat",
                              "SET DOLPHIN AND GAME PATH.bat", "start-smg2.ps1", "start-server.ps1"],
    "SMG2 Online - Mac": ["Start SMG2 Online.command", "Join A Friend.command", "start-smg2.sh"],
}
WII = "SMG2 Online - Wii and Wii U"
# What a pack starts out pointing at; the launchers and the console readme say how to change it
SERVER_ADDRESS = {"riivolution/serverIP.txt": b"127.0.0.1:5030", "SD-card/smg2online/serverIP.txt": b"192.168.0.2:5030"}


def add(pack, arc, path=None, data=None):
    info = zipfile.ZipInfo.from_file(path, arc) if path else zipfile.ZipInfo(arc, (2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    if arc.endswith((".sh", ".command")):
        info.external_attr = 0o100755 << 16  # stays executable when unzipped on a Mac
    if data is None:
        with open(path, "rb") as f:
            data = f.read()
    pack.writestr(info, data)


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, files in PACKS.items():
        with zipfile.ZipFile(os.path.join(OUT, name + ".zip"), "w") as pack:
            for f in files + COMMON:
                add(pack, f"{name}/{f}", os.path.join(ROOT, f))
            add(pack, f"{name}/riivolution/serverIP.txt", data=SERVER_ADDRESS["riivolution/serverIP.txt"])

    with zipfile.ZipFile(os.path.join(OUT, WII + ".zip"), "w") as pack:
        wii = os.path.join(ROOT, "wii")
        for folder, _dirs, files in os.walk(wii):
            for f in files:
                path = os.path.join(folder, f)
                rel = os.path.relpath(path, wii).replace(os.sep, "/")
                if rel not in SERVER_ADDRESS:
                    add(pack, f"{WII}/{rel}", path)
        add(pack, f"{WII}/SD-card/smg2online/serverIP.txt", data=SERVER_ADDRESS["SD-card/smg2online/serverIP.txt"])
        add(pack, f"{WII}/LICENSE", os.path.join(ROOT, "LICENSE"))

    for name in sorted(os.listdir(OUT)):
        if name.endswith(".zip"):
            with zipfile.ZipFile(os.path.join(OUT, name)) as pack:
                assert pack.testzip() is None
                print(f"{name}: {os.path.getsize(os.path.join(OUT, name))} bytes, {len(pack.namelist())} files")


if __name__ == "__main__":
    main()
