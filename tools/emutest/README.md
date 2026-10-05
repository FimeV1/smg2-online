# In-emulator test harness

Drives real Dolphin instances running the patched game without touching the
desktop: instances run on a hidden Windows desktop, and input, screenshots and
memory reads go through a Python-scripting build of Dolphin (any Felk-style
fork). Point `SMG2_TEST_DOLPHIN` at its `Dolphin.exe`, or put the path in
`dolphin-path.txt` here.

    py drive.py launch 1             # needs profiles\test1 (a Dolphin user dir)
    py goto_game.py 1 1              # title -> save file 1 -> in game
    py drive.py shot 1 name          # name-1.png from the emulator framebuffer
    py drive.py seq 1 "stick=0;1 wait:60 A:8"
    py click.py 1 630 405            # point at a screenshot pixel and press A
    py savepeek.py 1                 # star flags read from the game's memory
    py savepeek.py 1 give 3 0        # set a star, to watch it reach the others
    py drive.py kill [N]

Profiles: `profiles\testN` is a Dolphin user dir with `Config\` (WiimoteNew.ini
with an emulated Wii Remote + Nunchuk) and an SMG2 save in
`Wii\title\00010000\53423445\data`. Run a server first
(`server\smg2_server.py`); `riivolution\serverIP.txt` says where the games
look for it. `game\Super Mario Galaxy 2 (USA) (En,Fr,Es).rvz` is the game.

Screenshots can show a stale frame; take two in a row before trusting one.
OSReport output lands in `profiles\testN\Logs\dolphin.log`.
