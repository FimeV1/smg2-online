# SMG2 Online

Online multiplayer for **Super Mario Galaxy 2 (USA, SB4E01)**. Every player
runs their own copy of the game; a small server shows each player the other
Marios in the same galaxy and keeps everyone's save file in step, so a group
can play an any% run or a full 242-star file together.

It is a patch on the real game, loaded through Riivolution: your game file is
never modified, and you need your own dump or disc.

**Heavily developed using AI (local and cloud models).** Most of the code and
documentation here was written by AI under the author's direction. It is not
fully human-made, so read it and test it with that in mind.

* Cloud: Anthropic's Claude (Opus 5.5), used through Claude Code. The commits
  it wrote are marked `Co-Authored-By: Claude`.
* Local: Qwen 3.8 Flash-Next and Qwen 3.8 27B, run on the author's own PC.

> **This has not been play-tested yet.** Nobody has played a session with it,
> let alone a full run. So far it has only been checked by its author's
> automated tests and short two-game checks on one Windows PC (see
> [What has been tested](#what-has-been-tested)). The Mac launcher has never
> run on a Mac and the Wii / Wii U package has never been on a console.
> Expect bugs, back up any save you care about, and please report what you find.

| Platform | Status |
|---|---|
| Dolphin on Windows | tested (two games on one PC, in the real game) |
| Dolphin on macOS / Linux | launcher written and dry-run on Linux; **not yet run on a Mac** |
| Wii / Wii U (vWii) | package built; **not yet tried on a console** |

## Play

You need Dolphin, your own Super Mario Galaxy 2 (USA) as `.rvz`, `.iso` or
`.wbfs`, and Python 3 on the computer that hosts.

**Windows**

| What | How |
|---|---|
| Host + play | double-click `HOST A GAME.bat` |
| Host + 2 players on this PC | double-click `START SMG2 ONLINE.bat` |
| Join a friend | double-click `JOIN A FRIEND.bat` and type their address |
| Server only | `powershell -ExecutionPolicy Bypass -File start-server.ps1` |
| Change Dolphin / game location | `SET DOLPHIN AND GAME PATH.bat` |

**macOS / Linux**

| What | How |
|---|---|
| Host + play | double-click `Start SMG2 Online.command` (or `./start-smg2.sh`) |
| Join a friend | double-click `Join A Friend.command` (or `./start-smg2.sh --join`) |
| Everything else | `./start-smg2.sh --help` |

The first time, a Mac may refuse to open the `.command` files: right-click >
Open, or run `chmod +x *.command start-smg2.sh` in Terminal once.

**Wii / Wii U**: see `wii/READ ME - WII AND WII U.txt`.

Each local player gets their own Dolphin profile (Windows:
`%APPDATA%\SMG2-Online\profiles\pN`, Mac: `~/Library/Application
Support/SMG2-Online/profiles/pN`). On first launch it copies your normal
Dolphin settings and your SMG2 save into it, so **your regular save is never
touched**.

### Playing over the internet

The host forwards **UDP port 5030** on their router to their computer (or
everyone joins the same Tailscale / ZeroTier network) and shares their public
IP. Only the host needs Python.

### Server settings

`SERVER SETTINGS.bat` opens `server/server-settings.ini` (created the first
time the server runs). Restart the server after changing it.

| Setting | What it does |
|---|---|
| `port` | UDP port (default 5030). Players join with `ip:port`. |
| `max_players` | Connection limit, 1-250 (default 32). |
| `world` | Name of the shared world; each name keeps its own progress (`server/world-<name>.bin`). |
| `share_progress` | `no` = players only see each other and keep their own progress. |
| `merge_on_join` | `yes` = a joining player's existing progress is added to the world. `no` = the world wins; use it for races from a fresh file. |
| `player_timeout` | Seconds of silence before a player's slot is freed (default 30). |
| `update_rate` | Position updates per second, 10-30. |

To start a world over: `start-server.ps1 -Fresh` (the old one is kept as `.old`).

## In the game

* **Title screen**: the logo says ONLINE. It is grey until the server has
  answered and lights up when you are connected.
* **Other players** appear when you are in the same galaxy and the same star
  (Starship Mario counts as one place). Each player has their own outfit
  colours; you always see yourself in red and blue.
* **Mod menu**: press **−** (Minus) in a stage. **1** moves to the next line,
  **2** changes it, **−** closes. It shows your player number and how many
  are online, and lets you hide other players, turn the colours off, or stop
  sharing progress from this game.

## What is synced

Progress is not synced item by item. The game can write its whole save file
into one block of data and load it back; the mod sends that block to the
server whenever it changes, the server merges it into the shared world, and
every other game loads the result. So everything the save file holds is
covered:

| | |
|---|---|
| All 242 Power Stars, including Green Stars, comet stars and Grand Stars | yes |
| Comet Medals, bronze stars, galaxy unlock state, comets in orbit | yes |
| Every story / event flag (worlds entered, tutorials, endings, final galaxy) | yes |
| Hungry Luma feeding | yes |
| Star bit and coin stock | yes, as one shared pool: what anyone earns or spends is added or taken |
| Banker Toad, mail, Starship events, best times and scores | yes |
| Lives, play time, where your ship is on the world map | no, these stay yours |
| Player position and animation | yes, smoothed |
| Enemies, coins, objects, bosses in a stage | no, every player has their own stage |

An **any% run** needs a subset of this (stars, Grand Stars, world unlocks and
the ending flags); a **100% file** needs all of it. Both come from the same
mechanism, so there is no separate mode.

When two players change things at once, nothing is lost: flags follow whoever
changed them last, counters add up, best times keep the fastest, and star
counts only ever grow.

### Playing a run together

1. The host starts a fresh world: `start-server.ps1 -Fresh`, with
   `merge_on_join = no` if nobody should bring old progress in.
2. Everyone starts the game through the launcher and picks a save file.
3. Any star anyone collects reaches everyone within a second, with everything
   it unlocks. Received progress is in your game at once and is written to
   your save the next time the game saves.

A **new save file still plays the game's opening** (the story book and the
first level) even if the world is further along; the shared progress is
already in the file and takes effect as soon as the opening is over.

## Known limits

* USA version only. PAL and Japanese copies need their own addresses.
* Other players are drawn as your own character's model, recoloured. A friend
  playing as Luigi looks like a recoloured Mario to you, and power-up suits
  and Yoshi are not shown on other players.
* Players pass through each other, and star bits you shoot are not sent.
* Up to 7 other players are drawn in one place; the server accepts more.

## What has been tested

* `server/test_server.py`: 16 tests of the merge rules and delivery, including
  a full 242-star file reaching a fresh one, both players collecting at once,
  30% packet loss, a server restart, and garbage packets.
* In the real game on Dolphin, two instances: seeing each other with
  animation and colours, the mod menu and each of its settings, the title
  screen, a fresh save file receiving the world's stars, and both games
  gaining a different star at the same moment and ending with all of them
  (read back from each game's memory).
* **Not tested**: a full playthrough, more than two real games at once, a Mac,
  and a real Wii or Wii U.

## Troubleshooting

* **ONLINE stays grey / "no server yet" in the menu**: the server is not
  reachable. Check the server window is open, the address is right, and UDP
  5030 is forwarded. Allow Python through the firewall when asked.
* **Nobody shows up**: you must be in the same galaxy *and* star. The server
  window prints `player N joined` for every game.
* **"runs a different version of the mod" in the server window**: that player
  has an older copy; send them this folder again.

## Building

The client is C++ built with CodeWarrior and Kamek against
[Syati](https://github.com/SMGCommunity/Syati) (headers, symbols and loader
for SMG2). On Windows it builds inside WSL:

```
wsl -d Ubuntu-24.04 -e bash -lc 'cd ~/smg2/client && bash build.sh SB4E'
```

This writes `riivolution/CustomCode_SB4E.bin` + `riivo_SB4E.xml` (Dolphin) and
`wii/SD-card/` (console).

Three things are needed that are not in this repository:

* Syati, cloned into this folder: `git clone https://github.com/SMGCommunity/Syati`
* `Syati/deps/CodeWarrior/mwcceppc.exe` (the CodeWarrior PPC EABI compiler; it
  cannot be redistributed) and `Syati/deps/Kamek/Kamek`
* your own game. Nothing from the game is in this repository; `game/` and
  `dol/` (a dump extracted for the disassembler in `tools/`) are ignored by git.

| Path | What |
|---|---|
| `client/source/net.cpp` | UDP on two background threads (IOS sockets) |
| `client/source/session.cpp` | handshake, timeouts, packing records |
| `client/source/savesync.cpp` | save file reports and updates |
| `client/source/players.cpp` | sending your pose, drawing the others |
| `client/source/colours.cpp` | per-player outfit textures |
| `client/source/menu.cpp`, `title.cpp` | mod menu, title screen |
| `client/include/protocol.h` | wire format, shared with the server |
| `server/smg2_server.py` | the server |
| `server/savemerge.py` | save layout and merge rules |
| `tools/` | disassembler, archive reader, in-emulator test harness |

## Licence

MIT, see `LICENSE`. Syati and Kamek have their own licences; the game belongs
to Nintendo and no part of it is included.

## Credits

Built with [Syati](https://github.com/SMGCommunity/Syati) by the SMG modding
community and [Kamek](https://github.com/Treeki/Kamek) by Treeki. The idea
and the launcher follow the SMG1 mod built on Headpenguin's
[SMGNetworkMultiplayer](https://github.com/Headpenguin/SMGNetworkMultiplayer).
