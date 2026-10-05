#!/usr/bin/env bash
# Builds the SMG2 Online client (runs in WSL / Linux).
#
#   ./build.sh            build for SB4E (USA)
#   ./build.sh SB4P       another region (see Syati/symbols)
#   ./build.sh SB4E clean
#
# Output: ../riivolution/CustomCode_<REGION>.bin and riivo_<REGION>.xml
set -euo pipefail

REGION="${1:-SB4E}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
SYATI="$ROOT/Syati"
CC="$SYATI/deps/CodeWarrior/mwcceppc.exe"
KAMEK="$SYATI/deps/Kamek/Kamek"
OBJ="$HERE/obj/$REGION"
OUT="$ROOT/riivolution"
SYMBOLS="$OBJ/symbols.txt"

if [[ "${2:-}" == "clean" ]]; then
    rm -rf "$OBJ"
fi
mkdir -p "$OBJ" "$OUT"

# Syati's map plus the few symbols it lacks
cat "$SYATI/symbols/$REGION.txt" "$HERE/symbols/$REGION.txt" > "$SYMBOLS"

CFLAGS=(-c -Cpp_exceptions off -nodefaults -proc gekko -fp hard -lang=c++ -O4,s -inline on
        -rtti off -sdata 0 -sdata2 0 -align powerpc -func_align 4 -str pool -enum int
        -DGEKKO "-D$REGION" -i "$SYATI/include" -I- -i "$HERE/include")

# ---- loader (static patch that loads CustomCode_<REGION>.bin at boot) ----
"$CC" "${CFLAGS[@]}" -i "$SYATI/loader" "$SYATI/loader/loader.cpp" -o "$OBJ/loader.o"
"$KAMEK" "$OBJ/loader.o" -static=0x80001800 "-externals=$SYMBOLS" \
    "-output-riiv=$OBJ/loader.xml" > /dev/null

# ---- client ----
# A header change rebuilds everything; the whole build takes a few seconds.
newest_header="$(find "$HERE/include" -type f -printf '%T@\n' | sort -n | tail -1)"
objs=()
for src in "$HERE"/source/*.cpp; do
    obj="$OBJ/$(basename "${src%.cpp}").o"
    objs+=("$obj")
    if [[ ! -f "$obj" || "$src" -nt "$obj" || "$HERE/build.sh" -nt "$obj" ]] ||
       awk -v h="$newest_header" -v o="$(stat -c %Y "$obj")" 'BEGIN { exit !(h > o) }'; then
        echo "  CC  $(basename "$src")"
        rm -f "$obj"
        "$CC" "${CFLAGS[@]}" "$src" -o "$obj"
        [[ -f "$obj" ]] || { echo "compile failed: $src"; exit 1; }
    fi
done

"$KAMEK" "${objs[@]}" "-externals=$SYMBOLS" \
    "-output-kamek=$OUT/CustomCode_$REGION.bin" "-output-map=$OBJ/CustomCode.map" > "$OBJ/kamek.log" ||
    { cat "$OBJ/kamek.log"; exit 1; }

# ---- Riivolution XML ----
{
    cat <<EOF
<wiidisc version="1">
	<id game="${REGION:0:4}" />
	<options>
		<section name="SMG2 Online">
			<option name="SMG2 Online" id="smg2online">
				<choice name="Enabled">
					<patch id="smg2online" />
				</choice>
			</option>
		</section>
	</options>
	<patch id="smg2online">
EOF
    sed 's/^/\t\t/' "$OBJ/loader.xml"
    cat <<EOF
		<file disc="/CustomCode/CustomCode_$REGION.bin" external="CustomCode_$REGION.bin" create="true" />
		<file disc="/CustomCode/serverIP.txt" external="serverIP.txt" create="true" />
	</patch>
</wiidisc>
EOF
} > "$OUT/riivo_$REGION.xml"

[[ -f "$OUT/serverIP.txt" ]] || printf '127.0.0.1:5030\n' > "$OUT/serverIP.txt"

# ---- Wii / Wii U (vWii): files for the SD card, loaded by Riivolution ----
SD="$ROOT/wii/SD-card"
mkdir -p "$SD/riivolution" "$SD/smg2online"
{
    cat <<EOF
<wiidisc version="1" root="/smg2online">
	<id game="${REGION:0:4}" />
	<options>
		<section name="SMG2 Online">
			<option name="Online multiplayer" id="smg2online">
				<choice name="Enabled">
					<patch id="smg2online" />
				</choice>
			</option>
		</section>
	</options>
	<patch id="smg2online">
EOF
    sed 's/^/\t\t/' "$OBJ/loader.xml"
    cat <<EOF
		<file disc="/CustomCode/CustomCode_$REGION.bin" external="CustomCode_$REGION.bin" create="true" />
		<file disc="/CustomCode/serverIP.txt" external="serverIP.txt" create="true" />
	</patch>
</wiidisc>
EOF
} > "$SD/riivolution/SMG2Online_$REGION.xml"
cp "$OUT/CustomCode_$REGION.bin" "$SD/smg2online/"
[[ -f "$SD/smg2online/serverIP.txt" ]] || printf '192.168.0.2:5030\n' > "$SD/smg2online/serverIP.txt"

echo "built $OUT/CustomCode_$REGION.bin ($(stat -c %s "$OUT/CustomCode_$REGION.bin") bytes)"
