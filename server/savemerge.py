"""
Layout and merging of Super Mario Galaxy 2 save blobs.

A "blob" is one save file exactly as the game serializes it
(BinaryDataChunkHolder::makeFileBinary): a 4-byte header followed by six
chunks - PLAY (lives, star bit and coin stock), FLG1 (event flags), STF1
(Hungry Lumas), VLE1 (event values), GALA (every galaxy and star) and SSWM
(world map position).

The blob describes itself (field tables with name hashes), so the layout is
read from the blob, not hard-coded. Each field gets a merge rule:

    const   structure (signatures, hashes, sizes): must match, never merged
    local   stays private to each player and is zero in everything shared
    bits    flags: each bit follows whoever changed it last
    max     only ever grows (galaxy state, Hungry Luma feeding, best scores)
    min     only ever shrinks, 0 = not set (best times)
    add     counter: a player's change is added to the shared value
    lww     plain value: follows whoever changed it last

`merge(master, base, new)` folds the changes one player made (base -> new)
into the shared world. `merge_union(master, new)` combines a blob with no
known history, keeping the most progress of both.
"""
import struct

HEADER_SIZE = 4
CHUNK_HEADER = 12


def game_hash(name):
    """MR::getHashCode over the raw (Shift-JIS) bytes of a name."""
    if isinstance(name, str):
        name = name.encode("shift_jis")
    value = 0
    for c in name:
        value = (value * 31 + (c - 256 if c > 127 else c)) & 0xFFFFFFFF
    return value


def h16(name):
    return game_hash(name) & 0xFFFF


class Field:
    __slots__ = ("kind", "parts", "name", "limit")

    def __init__(self, kind, parts, name, limit=None):
        self.kind = kind
        self.parts = parts      # [(offset, size)], most significant first
        self.name = name
        self.limit = limit      # highest value of an `add` field

    @property
    def offset(self):
        return self.parts[0][0]

    def get(self, blob):
        value = 0
        for offset, size in self.parts:
            value = (value << (8 * size)) | int.from_bytes(blob[offset:offset + size], "big")
        return value

    def put(self, blob, value):
        for offset, size in reversed(self.parts):
            blob[offset:offset + size] = (value & ((1 << (8 * size)) - 1)).to_bytes(size, "big")
            value >>= 8 * size

    def __repr__(self):
        return f"Field({self.kind}, {self.name}, {self.parts})"


class LayoutError(ValueError):
    pass


# ---- event values (VLE1) -------------------------------------------------
# Values wider than 16 bits are stored as two entries, "<name>/hi" and
# "<name>/lo"; the hash of the lo name is always the hi hash + LO_MINUS_HI.
LO_MINUS_HI = (ord("l") - ord("h")) * 31 + (ord("o") - ord("i"))

VALUE_RULES = {}


def _value_rule(name, kind, wide=False, limit=None):
    VALUE_RULES[h16(name + "/hi") if wide else h16(name)] = (kind, name, limit, wide)


_value_rule("累積プレイ時間", "local", wide=True)                    # play time: ticks every frame
_value_rule("累積死亡回数", "add", limit=0xFFFF)                    # deaths
_value_rule("累積ゲームオーバー回数", "add", limit=0xFFFF)          # game overs
_value_rule("一定数死亡後のステージクリア回数", "add", limit=0xFFFF)
_value_rule("グライダー[ジャングル]", "min", wide=True)             # Fluzzard best times
_value_rule("グライダー[チャレンジ]", "min", wide=True)
for _galaxy in ("MokumokuValleyGalaxy", "HoneyBeeVillageGalaxy", "UnderGroundDangeonGalaxy",
                "TwisterTowerGalaxy", "KachikochiLavaGalaxy", "WhiteSnowGalaxy"):
    _value_rule(f"ベストスコア[{_galaxy}]", "max", wide=True)        # score attack bests
for _name in ("郵便屋[タスク手紙既読フラグ]/0", "郵便屋[タスク手紙既読フラグ]/1",
              "郵便屋[重要手紙既読フラグ]/0", "郵便屋[重要手紙既読フラグ]/1",
              "メッセージ既読フラグ/0", "メッセージ既読フラグ/1"):
    _value_rule(_name, "bits")                                       # read-mail and read-message bit sets

PLAY_RULES = {
    h16("mPlayerLeft"): ("local", "lives", None),
    h16("mStockedStarPieceNum"): ("add", "star bits", 9999),
    h16("mStockedCoinNum"): ("add", "coins", 9999),
    h16("mLast1upCoinNum"): ("local", "last 1-up coin count", None),
    h16("mFlag"): ("bits", "player flags", None),
}

GALAXY_RULES = {
    h16("mGalaxyName"): "const",
    h16("mDataSize"): "const",
    h16("mScenarioNum"): "const",
    h16("mGalaxyState"): "max",   # closed < new < opened
    h16("mFlag"): "bits",         # comet medal, comet in orbit
}

SCENARIO_RULES = {
    h16("mMissNum"): "max",
    h16("mBestTime"): "min",
    h16("mFlag"): "bits",         # star, bronze star, visited, ...
}

SCENARIO_FLAG_STAR = 0x01


def _read_table(blob, offset):
    """A BinaryDataContentHeaderSerializer table: [(name hash, offset, size)], data size, table size."""
    count, data_size = struct.unpack_from(">HH", blob, offset)
    entries = [struct.unpack_from(">HH", blob, offset + 4 + 4 * i) for i in range(count)]
    out = []
    for i, (name, start) in enumerate(entries):
        end = entries[i + 1][1] if i + 1 < count else data_size
        out.append((name, start, end - start))
    return out, data_size, 4 + 4 * count


class Layout:
    """Where every field of a blob is, and how it merges."""

    def __init__(self, blob):
        blob = bytes(blob)
        self.size = len(blob)
        self.fields = []
        self.kind_at = bytearray(len(blob))   # 0 = const, 1 = anything else
        self.galaxies = []                    # (name hash, [scenario flag Field])
        try:
            self._parse(blob)
        except (struct.error, IndexError) as err:
            raise LayoutError(f"not a save blob: {err}") from None
        for field in self.fields:
            for offset, size in field.parts:
                if offset + size > self.size:
                    raise LayoutError("field outside the blob")
                self.kind_at[offset:offset + size] = b"\1" * size
        self.const_mask = bytes(self.kind_at)
        self.local_ranges = self._ranges([f for f in self.fields if f.kind == "local"])

    def _add(self, kind, parts, name, limit=None):
        field = Field(kind, parts, name, limit)
        self.fields.append(field)
        return field

    def _parse(self, blob):
        if len(blob) < HEADER_SIZE or blob[0] != 2:
            raise LayoutError("unknown save format")
        offset = HEADER_SIZE
        for _ in range(blob[1]):
            signature, _hash, size = struct.unpack_from(">4sII", blob, offset)
            if size < CHUNK_HEADER or offset + size > len(blob):
                raise LayoutError("chunk outside the blob")
            parser = getattr(self, "_chunk_" + signature.decode("latin-1").strip(), None)
            if parser is None:
                raise LayoutError(f"unknown chunk {signature!r}")
            parser(blob, offset + CHUNK_HEADER, offset + size)
            offset += size
        if offset != len(blob):
            raise LayoutError("size does not match the chunks")

    def _chunk_PLAY(self, blob, start, end):
        table, data_size, table_size = _read_table(blob, start)
        data = start + table_size
        for name, offset, size in table:
            kind, label, limit = PLAY_RULES.get(name, ("lww", f"PLAY {name:04x}", None))
            self._add(kind, [(data + offset, size)], label, limit)

    def _chunk_FLG1(self, blob, start, end):
        # u16 per flag: 15 bits of name hash, top bit = on
        for offset in range(start, end - 1, 2):
            self._add("bits", [(offset, 1)], f"flag {struct.unpack_from('>H', blob, offset)[0] & 0x7FFF:04x}")

    def _chunk_STF1(self, blob, start, end):
        # Star bits fed to each Hungry Luma, then which ones are done
        counts_end = min(end, start + 0x60)
        for offset in range(start, counts_end, 2):
            self._add("max", [(offset, 2)], f"hungry luma {(offset - start) // 2}")
        for offset in range(counts_end, end):
            self._add("bits", [(offset, 1)], f"hungry luma flags {offset - counts_end}")

    def _chunk_VLE1(self, blob, start, end):
        entries = {}
        for offset in range(start, end - 3, 4):
            entries[struct.unpack_from(">H", blob, offset)[0]] = offset + 2
        used = set()
        for name, value_offset in entries.items():
            rule = VALUE_RULES.get(name)
            lo = (name + LO_MINUS_HI) & 0xFFFF
            if rule and rule[3] and lo in entries:
                self._add(rule[0], [(value_offset, 2), (entries[lo], 2)], rule[1], rule[2])
                used.update((name, lo))
        for name, value_offset in entries.items():
            if name in used:
                continue
            kind, label, limit, _wide = VALUE_RULES.get(name, ("lww", f"value {name:04x}", None, False))
            self._add(kind, [(value_offset, 2)], label, limit)

    def _chunk_GALA(self, blob, start, end):
        galaxy_count, = struct.unpack_from(">H", blob, start)
        galaxy_table, galaxy_size, size = _read_table(blob, start + 2)
        scenario_table, scenario_size, size2 = _read_table(blob, start + 2 + size)
        offset = start + 2 + size + size2

        names = {name: (o, s) for name, o, s in galaxy_table}
        name_at = names[h16("mGalaxyName")]
        count_at = names[h16("mScenarioNum")]
        for _ in range(galaxy_count):
            galaxy_name = int.from_bytes(blob[offset + name_at[0]:offset + name_at[0] + name_at[1]], "big")
            scenario_count = blob[offset + count_at[0]]
            for name, o, s in galaxy_table:
                kind = GALAXY_RULES.get(name, "lww")
                if kind != "const":
                    self._add(kind, [(offset + o, s)], f"galaxy {galaxy_name:04x} {name:04x}")
            offset += galaxy_size
            stars = []
            for scenario in range(scenario_count):
                for name, o, s in scenario_table:
                    kind = SCENARIO_RULES.get(name, "lww")
                    field = self._add(kind, [(offset + o, s)], f"galaxy {galaxy_name:04x} star {scenario + 1} {name:04x}")
                    if name == h16("mFlag"):
                        stars.append(field)
                offset += scenario_size
            self.galaxies.append((galaxy_name, stars))
        if offset != end:
            raise LayoutError("galaxy data does not fill its chunk")

    def _chunk_SSWM(self, blob, start, end):
        # Where this player's ship is on the world map
        self._add("local", [(start, end - start)], "world map position")

    @staticmethod
    def _ranges(fields):
        spans = sorted((o, o + s) for f in fields for o, s in f.parts)
        out = []
        for start, end in spans:
            if out and out[-1][1] == start:
                out[-1][1] = end
            else:
                out.append([start, end])
        return [(start, end - start) for start, end in out]

    # ---- checks ----------------------------------------------------------
    def matches(self, blob):
        """True if `blob` has this layout (same size and structure bytes)."""
        if len(blob) != self.size:
            return False
        return all(mask or a == b for mask, a, b in zip(self.const_mask, blob, self._reference))

    def set_reference(self, blob):
        self._reference = bytes(blob)

    def canonical(self, blob):
        """`blob` with every private field zeroed."""
        out = bytearray(blob)
        for offset, size in self.local_ranges:
            out[offset:offset + size] = bytes(size)
        return out

    def count_stars(self, blob):
        return sum(1 for _name, stars in self.galaxies for f in stars if f.get(blob) & SCENARIO_FLAG_STAR)

    # ---- merging ---------------------------------------------------------
    def merge(self, master, base, new):
        """Fold the changes base -> new into master. Returns the changed fields."""
        changed = []
        for field in self.fields:
            if field.kind == "local":
                continue
            old, value = field.get(base), field.get(new)
            if old == value:
                continue
            current = field.get(master)
            if field.kind == "bits":
                diff = old ^ value
                result = (current & ~diff) | (value & diff)
            elif field.kind == "max":
                result = max(current, value)
            elif field.kind == "min":
                result = value if current == 0 else (current if value == 0 else min(current, value))
            elif field.kind == "add":
                result = max(0, min(field.limit or 0xFFFFFFFF, current + value - old))
            else:
                result = value
            if result != current:
                field.put(master, result)
                changed.append(field)
        return changed

    def merge_union(self, master, new):
        """Combine with a blob of unknown history, keeping the most progress."""
        changed = []
        for field in self.fields:
            if field.kind == "local":
                continue
            current, value = field.get(master), field.get(new)
            if current == value:
                continue
            if field.kind == "bits":
                result = current | value
            elif field.kind == "min":
                result = value if current == 0 else (current if value == 0 else min(current, value))
            else:
                # max, add and lww: counters and progress values only grow
                result = max(current, value)
            if result != current:
                field.put(master, result)
                changed.append(field)
        return changed
