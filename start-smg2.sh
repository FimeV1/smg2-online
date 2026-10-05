#!/usr/bin/env bash
# =====================================================================
#  SMG2 Online - launcher for macOS and Linux
#
#    ./start-smg2.sh                         host a server + 1 player
#    ./start-smg2.sh --players 2             host + 2 players on this computer
#    ./start-smg2.sh --join                  ask for a server address and join it
#    ./start-smg2.sh --server 203.0.113.7:5030
#    ./start-smg2.sh --set-paths             change where Dolphin and the game are
#    ./start-smg2.sh --server-only           run just the server in this window
#
#  1. Starts the server (server/smg2_server.py) if we are hosting
#  2. Writes the server address into riivolution/serverIP.txt
#  3. Boots one Dolphin per local player straight into patched SMG2, each
#     with its own profile so saves and settings never collide
#
#  On a Mac, double-click "Start SMG2 Online.command" instead of typing this.
# =====================================================================
set -euo pipefail

BASE="$(cd "$(dirname "$0")" && pwd)"
PLAYERS=1
SERVER=""
PORT=""
JOIN=0
SET_PATHS=0
SERVER_ONLY=0
DRY_RUN=0
DOLPHIN="${DOLPHIN:-}"
GAME="${GAME:-}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --players) PLAYERS="$2"; shift 2 ;;
        --server) SERVER="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        --dolphin) DOLPHIN="$2"; shift 2 ;;
        --game) GAME="$2"; shift 2 ;;
        --join) JOIN=1; shift ;;
        --set-paths) SET_PATHS=1; shift ;;
        --server-only) SERVER_ONLY=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1 (try --help)" >&2; exit 1 ;;
    esac
done

if [[ "$(uname)" == "Darwin" ]]; then
    DATA="$HOME/Library/Application Support/SMG2-Online"
    GLOBAL_USER="$HOME/Library/Application Support/Dolphin"
else
    DATA="${XDG_DATA_HOME:-$HOME/.local/share}/smg2-online"
    GLOBAL_USER="${XDG_DATA_HOME:-$HOME/.local/share}/dolphin-emu"
    GLOBAL_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/dolphin-emu"
fi
SETTINGS="$DATA/settings.txt"
mkdir -p "$DATA"

PYTHON="$(command -v python3 || command -v python || true)"

run_server() {
    [[ -n "$PYTHON" ]] || { echo "Python 3 is needed to host the server (https://www.python.org/downloads/)." >&2; exit 1; }
    local args=("$BASE/server/smg2_server.py")
    [[ -n "$PORT" ]] && args+=(--port "$PORT")
    "$PYTHON" "${args[@]}"
}

if [[ $SERVER_ONLY -eq 1 ]]; then
    run_server
    exit 0
fi

# ---- remembered paths and last server ---------------------------------
saved() { [[ -f "$SETTINGS" ]] && sed -n "s/^$1=//p" "$SETTINGS" | head -1 || true; }
save_settings() {
    {
        echo "dolphin=$DOLPHIN"
        echo "game=$GAME"
        echo "server=$LAST_SERVER"
    } > "$SETTINGS"
}
LAST_SERVER="$(saved server)"

ask_path() {  # ask_path "what" "current"
    local answer
    while true; do
        if [[ -n "$2" ]]; then
            read -r -p "$1 [now: $2] (drag the file here, or press Enter to keep): " answer
            [[ -z "$answer" ]] && { echo "$2"; return; }
        else
            read -r -p "Where is $1? Drag the file into this window and press Enter: " answer
        fi
        answer="${answer%\"}"; answer="${answer#\"}"; answer="${answer%\'}"; answer="${answer#\'}"
        answer="${answer//\\ / }"; answer="${answer% }"
        [[ -e "$answer" ]] && { echo "$answer"; return; }
        echo "      Not found, try again." >&2
    done
}

first_existing() {
    local c
    for c in "$@"; do
        [[ -n "$c" && -e "$c" ]] && { echo "$c"; return; }
    done
    true
}

GAME_NAME="Super Mario Galaxy 2 (USA) (En,Fr,Es)"
DOLPHIN="$(first_existing "$DOLPHIN" "$(saved dolphin)" "/Applications/Dolphin.app" "$HOME/Applications/Dolphin.app" \
    "$(command -v dolphin-emu || true)")"
GAME="$(first_existing "$GAME" "$(saved game)" "$BASE/game/$GAME_NAME.rvz" "$HOME/Downloads/$GAME_NAME.rvz" \
    "$HOME/Downloads/$GAME_NAME.wbfs" "$HOME/Downloads/$GAME_NAME.iso")"

if [[ $SET_PATHS -eq 1 ]]; then
    DOLPHIN="$(ask_path "Dolphin (the app)" "$DOLPHIN")"
    GAME="$(ask_path "Your Super Mario Galaxy 2 (USA) game file (.rvz/.wbfs/.iso)" "$GAME")"
    save_settings
    echo "Saved. You will not be asked again."
    exit 0
fi
[[ -n "$DOLPHIN" ]] || DOLPHIN="$(ask_path "Dolphin (the app)" "")"
[[ -n "$GAME" ]] || GAME="$(ask_path "your Super Mario Galaxy 2 (USA) game file (.rvz/.wbfs/.iso)" "")"

# Dolphin.app is a folder; the program is inside it
DOLPHIN_BIN="$DOLPHIN"
[[ -d "$DOLPHIN" ]] && DOLPHIN_BIN="$DOLPHIN/Contents/MacOS/Dolphin"
[[ -x "$DOLPHIN_BIN" ]] || { echo "Cannot run Dolphin at $DOLPHIN" >&2; exit 1; }

if [[ $JOIN -eq 1 ]]; then
    while [[ -z "$SERVER" ]]; do
        if [[ -n "$LAST_SERVER" ]]; then
            read -r -p "Server address (press Enter for $LAST_SERVER): " SERVER
            SERVER="${SERVER:-$LAST_SERVER}"
        else
            read -r -p "Server address (e.g. 203.0.113.7 or 203.0.113.7:5030): " SERVER
        fi
    done
fi
[[ -n "$SERVER" ]] && LAST_SERVER="$SERVER"
save_settings

[[ "$PLAYERS" =~ ^[1-8]$ ]] || { echo "--players must be between 1 and 8 on one computer." >&2; exit 1; }

# ---- 1: server ----------------------------------------------------------
if [[ -n "$SERVER" ]]; then
    ADDRESS="$SERVER"
    [[ "$ADDRESS" == *:* ]] || ADDRESS="$ADDRESS:${PORT:-5030}"
    echo "[1/3] Joining server $ADDRESS"
else
    if [[ -z "$PORT" ]]; then
        # (no settings file before the server has run once: that is fine)
        PORT="$(sed -n 's/^[[:space:]]*port[[:space:]]*=[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$BASE/server/server-settings.ini" 2>/dev/null | head -1 || true)"
        PORT="${PORT:-5030}"
    fi
    ADDRESS="127.0.0.1:$PORT"
    echo "[1/3] Hosting on this computer (UDP $PORT)..."
    [[ -n "$PYTHON" ]] || { echo "Python 3 is needed to host the server (https://www.python.org/downloads/)." >&2; exit 1; }
    if [[ $DRY_RUN -eq 1 ]]; then
        echo "      (dry run: not starting the server)"
    elif pgrep -f "smg2_server.py" > /dev/null 2>&1; then
        echo "      server already up."
    elif [[ "$(uname)" == "Darwin" ]]; then
        # Its own Terminal window: shows who joins, and closing it stops the server
        osascript -e "tell application \"Terminal\" to do script \"cd '$BASE' && ./start-smg2.sh --server-only --port $PORT\"" > /dev/null
        sleep 2
    else
        "$PYTHON" "$BASE/server/smg2_server.py" --port "$PORT" &
        echo "      server running in the background (stops with: pkill -f smg2_server.py)"
        sleep 2
    fi
fi

# The game reads this file (through Riivolution) when it boots
printf '%s' "$ADDRESS" > "$BASE/riivolution/serverIP.txt"
echo "      serverIP.txt -> $ADDRESS"

# ---- 2: Dolphin game preset ----------------------------------------------
PRESET="$BASE/smg2-online.json"
json_escape() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
cat > "$PRESET" <<EOF
{
    "type": "dolphin-game-mod-descriptor",
    "version": 1,
    "base-file": "$(json_escape "$GAME")",
    "display-name": "SMG2 Online (USA)",
    "riivolution": {
        "patches": [
            {
                "xml": "$(json_escape "$BASE/riivolution/riivo_SB4E.xml")",
                "root": "$(json_escape "$BASE/riivolution")",
                "options": [{ "section-name": "SMG2 Online", "option-id": "smg2online", "choice": 1 }]
            }
        ]
    }
}
EOF

# ---- 3: one Dolphin per player -------------------------------------------
SAVE_REL="Wii/title/00010000/53423445"   # SB4E = Super Mario Galaxy 2 (USA)
for ((i = 1; i <= PLAYERS; i++)); do
    USER_DIR="$DATA/profiles/p$i"
    if [[ ! -d "$USER_DIR" ]]; then
        # First run for this player: start from your normal Dolphin settings
        # (controller mapping, graphics) and your existing SMG2 save, as copies.
        mkdir -p "$USER_DIR/Config"
        for f in Dolphin.ini GFX.ini WiimoteNew.ini GCPadNew.ini Hotkeys.ini; do
            for dir in "${GLOBAL_CONFIG:-$GLOBAL_USER/Config}" "$GLOBAL_USER/Config"; do
                [[ -f "$dir/$f" && ! -f "$USER_DIR/Config/$f" ]] && cp "$dir/$f" "$USER_DIR/Config/$f"
            done
        done
        if [[ -d "$GLOBAL_USER/$SAVE_REL" ]]; then
            mkdir -p "$(dirname "$USER_DIR/$SAVE_REL")"
            cp -R "$GLOBAL_USER/$SAVE_REL" "$USER_DIR/$SAVE_REL"
        fi
        echo "      created profile p$i ($USER_DIR)"
    fi
    echo "[2/3] Launching player $i..."
    [[ $DRY_RUN -eq 1 ]] && continue
    "$DOLPHIN_BIN" -u "$USER_DIR" -e "$PRESET" > /dev/null 2>&1 &
    disown || true
    [[ $i -lt $PLAYERS ]] && sleep 3   # stagger disc access
done

echo
echo "[3/3] Done. Players see each other when they are in the same galaxy and star."
if [[ -z "$SERVER" ]]; then
    echo "      Keep the server window open while playing."
    echo "      Friends join with your public IP (port $PORT, UDP, must be forwarded on your router)."
fi
