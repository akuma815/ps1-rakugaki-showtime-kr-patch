#!/usr/bin/env python3
"""Build the full Korean atlas, decoder remap and re-encoded strings (HANGUL-ATLAS-001).

Design (supersedes the two-VRAM-page plan):

The stock 4bpp font atlas stores FOUR jittered 1-bit variants of each glyph in the
four bitplanes of a cell; the shared primitive builder picks palette 0-3 from a
global counter, which is what makes the doodle text wobble. Palettes 0-3 are the
only CLUT values the game ever writes (single site, ARA 0x80043004), so the plane
selection is fully ours to redefine.

We therefore pack TWO glyphs per cell with TWO jitter frames each:
    plane 0,1 -> glyph A (bank 0)      plane 2,3 -> glyph B (bank 1)
    palette   = (bank << 1) | (anim & 1)
Capacity becomes 255 cells x 2 = 510 glyphs in the EXISTING atlas page (960,256),
so no second VRAM page, no asset-directory relocation and no VRAM occupancy risk.
The wobble survives with two frames instead of four.

Encoding: every string is re-encoded to our own two-byte codes
    code = 0x8000 + ((bank << 8) | cell)
and the decoder collapses to `retval = code - 0x8000` with an out-of-range
fallback to the blank cell 0xFF.
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import math
import random
import re
import shutil
import struct
import sys
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from build_two_bank_poc import assemble, changed_ranges, disassemble, patch_extent_on_disc, sha256_bytes, sha256_file
from render_menu_strips import TRANSLATIONS, encode_strip_4bpp, quantize_4level, render_text_strip
from render_hit_effects import encode_bigfont_4bpp, encode_effect_4bpp, encode_marker_4bpp
from psx_disc import read_extent
from psx_tim import parse_tim


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


CODE_BASE = 0x8000
BLANK_CELL = 0xFF
WOBBLE_THRESHOLD_DELTAS = (-12, 20)   # frame 0 slightly bolder, frame 1 slightly thinner
WOBBLE_DIRECTIONS = ((1, 0), (0, 1), (1, 1), (-1, 0), (0, -1), (-1, -1), (1, -1), (-1, 1))
DECODER_ARA_OFFSET = 0x6D0DC
DECODER_RUNTIME = 0x800430DC
BUILDER_ARA_OFFSET = 0x6CF84
BUILDER_RUNTIME = 0x80042F84
BUILDER_WINDOW_BYTES = 0x88
FONT_TIM_HAGECHO_OFFSET = 0x34A98

# Hand-encoded: keystone expands `addiu rt, rs, 0x8000` into ori+addu and pads
# branch delay slots on its own, so the decoder words are written literally and
# verified by disassembly instead.
DECODER_WORDS = [
    0x24828000,  # addiu $v0, $a0, -0x8000      v0 = code - 0x8000
    0x2C430200,  # sltiu $v1, $v0, 0x200        in range?
    0x14600002,  # bnez  $v1, +2                -> jr $ra
    0x00000000,  # nop                          (delay slot)
    0x240200FF,  # addiu $v0, $zero, 0xff       out of range -> blank cell
    0x03E00008,  # jr    $ra
    0x00000000,  # nop                          (delay slot)
]
DECODER_EXPECTED = [
    "addiu $v0, $a0, -0x8000",
    "sltiu $v1, $v0, 0x200",
    "bnez $v1, 0x800430f0",
    "nop",
    "addiu $v0, $zero, 0xff",
    "jr $ra",
    "nop",
]

BUILDER_CODE = [
    "addiu $v0, $zero, 0x1f",
    "sh $v0, 0x16($t0)",
    "sll $v0, $s1, 4",
    "andi $v0, $v0, 0xf0",
    "sb $v0, 0xc($t0)",
    "sb $v0, 0x1c($t0)",
    "addiu $v1, $v0, 0xf",
    "sb $v1, 0x14($t0)",
    "sb $v1, 0x24($t0)",
    "andi $v0, $s1, 0xf0",
    "sb $v0, 0xd($t0)",
    "sb $v0, 0x15($t0)",
    "addiu $v1, $v0, 0xf",
    "sb $v1, 0x1d($t0)",
    "sb $v1, 0x25($t0)",
    "lui $v0, 0x800b",
    "lw $v0, -0x1310($v0)",
    "srl $v1, $s1, 7",
    "andi $v1, $v1, 2",
    "srl $v0, $v0, 2",
    "andi $v0, $v0, 1",
    "or $v0, $v0, $v1",
    "sll $v0, $v0, 6",
    "ori $v0, $v0, 0x7c12",
    "sh $v0, 0xe($t0)",
] + ["nop"] * 9


def render_planes(character: str, font: ImageFont.FreeTypeFont, threshold: int) -> list[list[list[int]]]:
    """Render the two wobble frames of one glyph.

    The stock atlas stores hand-drawn variants whose strokes ripple and whose
    ink weight changes between frames, so a rigid translation reads as "no
    wobble" on screen. Each frame therefore gets its own deterministic wavy
    warp (per-row dx, per-column dy) plus a slightly different threshold, which
    reproduces the doodle feel and keeps characters from moving in lockstep.
    """
    bbox = font.getbbox(character)
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if width <= 0 or height <= 0 or width > 16 or height > 16:
        raise ValueError(f"glyph {character!r} does not fit a 16x16 cell: {width}x{height}")
    base = Image.new("L", (16, 16), 0)
    ImageDraw.Draw(base).text(((16 - width) // 2 - bbox[0], (16 - height) // 2 - bbox[1]),
                              character, font=font, fill=255)
    pixels = base.load()
    # Each glyph picks its own displacement direction so characters do not all
    # move the same way on the same frame, and the two frames differ in stroke
    # weight as the stock hand-drawn variants do. The glyph is displaced as a
    # whole: per-row warping at 16 px breaks strokes and destroys legibility.
    direction = random.Random(character).choice(WOBBLE_DIRECTIONS)
    planes = []
    for frame, weight in enumerate(WOBBLE_THRESHOLD_DELTAS):
        dx, dy = (0, 0) if frame == 0 else direction
        level = max(24, min(224, threshold + weight))
        grid = [[0] * 16 for _ in range(16)]
        ink = 0
        for y in range(16):
            for x in range(16):
                if pixels[x, y] < level:
                    continue
                nx = min(15, max(0, x + dx))
                ny = min(15, max(0, y + dy))
                grid[ny][nx] = 1
                ink += 1
        if ink == 0:
            raise ValueError(f"glyph {character!r} rendered no ink at threshold {level}")
        planes.append(grid)
    return planes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--threshold", type=int, default=128)
    parser.add_argument("--bigfont-markers", action="store_true",
                        help="stamp each big-font cell with its own index instead of translating it; "
                             "words then spell out their cell sequence on screen (BIGFONT-WORDS-002). "
                             "Diagnostic only -- writes to a separate build id so the real disc is untouched.")
    parser.add_argument("--no-cheats", action="store_true",
                        help="build the Korean translation without the eight automatic unlock patches")
    args = parser.parse_args()
    if args.bigfont_markers and args.no_cheats:
        parser.error("--bigfont-markers and --no-cheats cannot be combined")
    project = args.project.resolve()
    manifest = json.loads((project / "workspace/config/source_manifest.json").read_text(encoding="utf-8"))
    coverage = json.loads((project / "work/analysis/font_coverage_001.json").read_text(encoding="utf-8"))
    plan = coverage["glyph_plan"]
    source_bin = project / manifest["disc"]["bin"]
    source_cue = project / manifest["disc"]["cue"]
    ara_path = project / "extracted/ARA.BIN"
    hagecho_path = project / "extracted/HAGECHO.BIN"
    for path, expected in [(source_bin, manifest["disc"]["bin_sha256"]),
                           (ara_path, manifest["expected_files"]["ARA.BIN"]["sha256"]),
                           (hagecho_path, manifest["expected_files"]["HAGECHO.BIN"]["sha256"])]:
        if sha256_file(path) != expected:
            raise ValueError(f"source hash mismatch: {path}")

    with (project / "workspace/text/rakugaki_text_ja_ko.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    # --- select rows that fit their original byte budget ---
    applied, deferred = [], []
    for row in rows:
        original_chars = len(row["original_hex"]) // 4
        translation = unicodedata.normalize("NFC", row["translation"])
        if not translation:
            deferred.append({**row, "reason": "empty translation"})
        elif len(translation) > original_chars:
            deferred.append({**row, "reason": f"overflow {len(translation)} > {original_chars} chars"})
        else:
            applied.append({**row, "normalized": translation, "original_chars": original_chars})

    # --- glyph inventory and code assignment ---
    glyphs = sorted({ch for row in applied for ch in row["normalized"] if ch not in (" ", "\u3000")})
    missing = [ch for ch in glyphs if ch not in plan]
    if missing:
        raise ValueError(f"glyphs missing from the coverage plan: {missing[:10]}")
    capacity = 255 * 2
    if len(glyphs) > capacity:
        raise ValueError(f"glyph count {len(glyphs)} exceeds packed capacity {capacity}")
    assignment = {}
    for index, ch in enumerate(glyphs):
        cell, bank = index >> 1, index & 1
        if cell >= BLANK_CELL:
            raise ValueError("glyph assignment reached the reserved blank cell")
        assignment[ch] = {"index": index, "cell": cell, "bank": bank,
                          "retval": (bank << 8) | cell, "code": CODE_BASE + ((bank << 8) | cell)}
    space_code = CODE_BASE + BLANK_CELL
    for ch in (" ", "\u3000"):
        assignment[ch] = {"index": None, "cell": BLANK_CELL, "bank": 0,
                          "retval": BLANK_CELL, "code": space_code}

    # --- render the packed atlas ---
    fonts: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
    font_dir = Path(__file__).resolve().parents[2] / "assets/fonts"
    cells: dict[int, list[list[int]]] = {}
    for ch in glyphs:
        spec = plan[ch]
        key = (spec["font"], spec["size_px"])
        font = fonts.setdefault(key, ImageFont.truetype(str(font_dir / spec["font"]), spec["size_px"]))
        planes = render_planes(ch, font, args.threshold)
        info = assignment[ch]
        grid = cells.setdefault(info["cell"], [[0] * 16 for _ in range(16)])
        shift = info["bank"] * 2
        for plane_index, plane in enumerate(planes):
            for y in range(16):
                for x in range(16):
                    if plane[y][x]:
                        grid[y][x] |= 1 << (shift + plane_index)

    hagecho = bytearray(hagecho_path.read_bytes())
    tim = parse_tim(bytes(hagecho), FONT_TIM_HAGECHO_OFFSET)
    if tim is None or tim["bpp"] != 4 or tim["pixel_width"] != 256 or tim["pixel_height"] != 256:
        raise ValueError("stock font TIM structure mismatch")
    image_offset = tim["image"]["payload_offset"]
    stride = tim["image"]["width_words"] * 2
    for offset in range(image_offset, image_offset + tim["image"]["payload_size"]):
        hagecho[offset] = 0
    for cell, grid in cells.items():
        cell_x, cell_y = (cell & 0xF) * 16, (cell >> 4) * 16
        for y in range(16):
            for x in range(16):
                value = grid[y][x]
                px, py = cell_x + x, cell_y + y
                position = image_offset + py * stride + px // 2
                old = hagecho[position]
                hagecho[position] = (old & 0x0F) | (value << 4) if px & 1 else (old & 0xF0) | value

    # --- re-encode strings in place ---
    ara = bytearray(ara_path.read_bytes())
    encoded = []
    for row in applied:
        target = ara if row["source"] == "ARA.BIN" else hagecho
        offset = int(row["file_offset_hex"], 16)
        original = bytes.fromhex(row["original_hex"])
        if bytes(target[offset:offset + len(original)]) != original:
            raise ValueError(f"string precondition mismatch at {row['id']}")
        payload = b"".join(struct.pack(">H", assignment[ch]["code"]) for ch in row["normalized"])
        # Entries such as the packed digit table share bytes with their neighbours and
        # carry no terminator of their own, so never write outside the row's own span:
        # a shorter translation terminates inside the span, an exact-length one relies
        # on whatever terminator the original already had.
        if len(payload) < len(original):
            payload += b"\x00\x00"
        if len(payload) > len(original):
            raise ValueError(f"re-encoded string overflows at {row['id']}")
        target[offset:offset + len(payload)] = payload
        encoded.append({"id": row["id"], "source": row["source"], "offset": offset,
                        "chars": len(row["normalized"]), "bytes": len(payload),
                        "terminator_written": len(payload) < len(original)})

    # --- patch decoder and builder ---
    decoder_patch = b"".join(struct.pack("<I", word) for word in DECODER_WORDS)
    decoded = disassemble(decoder_patch, DECODER_RUNTIME)
    if decoded != DECODER_EXPECTED:
        raise ValueError(f"decoder patch disassembly mismatch: {decoded}")
    ara[DECODER_ARA_OFFSET:DECODER_ARA_OFFSET + len(decoder_patch)] = decoder_patch
    builder_patch = assemble(BUILDER_CODE, BUILDER_RUNTIME)
    if len(builder_patch) != BUILDER_WINDOW_BYTES:
        raise ValueError(f"builder patch is {len(builder_patch)} bytes, expected {BUILDER_WINDOW_BYTES}")
    if disassemble(builder_patch, BUILDER_RUNTIME) != BUILDER_CODE:
        raise ValueError("builder patch round-trip mismatch")
    ara[BUILDER_ARA_OFFSET:BUILDER_ARA_OFFSET + len(builder_patch)] = builder_patch

    # --- patch pre-rendered menu label bitmaps (raw 4bpp, uploaded to (704,256)) ---
    menu_catalog = json.loads((project / "work/analysis/menu_strips_catalog.json").read_text(encoding="utf-8"))
    menu_records = []
    for strip in menu_catalog["entries"]:
        korean = TRANSLATIONS.get(strip.get("label"))
        if korean is None:
            continue
        # gap-visual strips read as clean horizontal Japanese, which proves the
        # stride is right; the SHA precondition below guarantees we only ever
        # overwrite the exact bytes we catalogued, so patching them is safe.
        offset = int(strip["disc_offset"], 16)
        stride = strip["stride_bytes"]
        length = stride * 16
        original = bytes(hagecho[offset:offset + length])
        if hashlib.sha256(original).hexdigest() != strip["sha256"]:
            raise ValueError(f"menu strip precondition mismatch at {strip['disc_offset']} ({strip.get('label')})")
        encoded = encode_strip_4bpp(korean, stride)
        if len(encoded) != length:
            raise ValueError(f"encoded menu strip wrong size for {korean}")
        hagecho[offset:offset + length] = encoded
        menu_records.append({"offset": strip["disc_offset"], "label": strip["label"],
                             "korean": korean, "width_px": stride * 2, "evidence": strip["evidence"]})

    # --- patch the separate 8-row labels used only on character select ---
    charselect_cfg = json.loads((project / "workspace/config/charselect_labels.json").read_text(encoding="utf-8"))
    charselect_records = []
    for strip in charselect_cfg["labels"]:
        offset = int(strip["disc_offset"], 16)
        stride = strip["stride_bytes"]
        height = strip["height_rows"]
        length = stride * height
        original = bytes(hagecho[offset:offset + length])
        if hashlib.sha256(original).hexdigest() != strip["sha256"]:
            raise ValueError(f"character-select label precondition mismatch: {strip['japanese']}")
        encoded = encode_strip_4bpp(strip["korean"], stride, height=height)
        if len(encoded) != length:
            raise ValueError(f"encoded character-select label wrong size for {strip['korean']}")
        hagecho[offset:offset + length] = encoded
        charselect_records.append({"offset": strip["disc_offset"], "japanese": strip["japanese"],
                                   "korean": strip["korean"], "box": [strip["width_px"], height],
                                   "role": strip["role"],
                                   "evidence": charselect_cfg["observation_id"]})

    # --- patch word boxes inside the block 0 banner TIM (goal / next stage / loading) ---
    banner_cfg = json.loads((project / "workspace/config/banner_words.json").read_text(encoding="utf-8"))
    banner_payload = int(banner_cfg["payload_offset"], 16)
    banner_stride = banner_cfg["stride_bytes"]
    banner_records = []

    def read_nibble(x: int, y: int) -> int:
        byte = hagecho[banner_payload + y * banner_stride + x // 2]
        return (byte >> 4) & 0xF if x & 1 else byte & 0xF

    def write_nibble(x: int, y: int, value: int) -> None:
        index = banner_payload + y * banner_stride + x // 2
        old = hagecho[index]
        hagecho[index] = (old & 0x0F) | (value << 4) if x & 1 else (old & 0xF0) | value

    for word in banner_cfg["words"]:
        box = bytes(read_nibble(x, y)
                    for y in range(word["y"], word["y"] + word["h"])
                    for x in range(word["x"], word["x"] + word["w"]))
        if hashlib.sha256(box).hexdigest() != word["ink_sha256"]:
            raise ValueError(f"banner word precondition mismatch: {word['name']}")
        # Keep the artwork's own ink colour: the most common non-zero index, not
        # max(), because anti-aliased edges use a few brighter indices.
        ink_counts = collections.Counter(value for value in box if value)
        ink_value = ink_counts.most_common(1)[0][0] if ink_counts else 0xF
        strip = render_text_strip(word["korean"], word["w"], height=word["h"])
        grid = quantize_4level(strip, 110)
        # Only the glyph pixels belong to us. Blanket-clearing the box also wipes
        # the background plates and decorations the game draws from this texture
        # (indices 4/14/15 here), which made the loading and next-stage
        # backgrounds vanish, so every other index is left untouched.
        for y in range(word["h"]):
            for x in range(word["w"]):
                original = box[y * word["w"] + x]
                if original == ink_value:
                    write_nibble(word["x"] + x, word["y"] + y, 0)
        for y in range(word["h"]):
            for x in range(word["w"]):
                original = box[y * word["w"] + x]
                if grid[y][x] and original in (0, ink_value):
                    write_nibble(word["x"] + x, word["y"] + y, ink_value)
        banner_records.append({"label": word["name"], "korean": word["korean"],
                               "box": [word["x"], word["y"], word["w"], word["h"]],
                               "ink_index": ink_value})

    # --- patch the duplicate UI/staff-roll word strips (STAFFROLL-STRIPS-001) ---
    staffroll_cfg = json.loads((project / "workspace/config/staffroll_strips.json").read_text(encoding="utf-8"))
    staffroll_records = []
    for strip in staffroll_cfg["strips"]:
        offset = int(strip["disc_offset"], 16)
        length = strip["stride_bytes"] * 16
        original = bytes(hagecho[offset:offset + length])
        if hashlib.sha256(original).hexdigest() != strip["sha256"]:
            raise ValueError(f"staff-roll strip precondition mismatch: {strip['japanese']}")
        encoded = encode_strip_4bpp(strip["korean"], strip["stride_bytes"])
        if len(encoded) != length:
            raise ValueError(f"encoded staff-roll strip wrong size for {strip['korean']}")
        hagecho[offset:offset + length] = encoded
        staffroll_records.append({"offset": strip["disc_offset"], "japanese": strip["japanese"],
                                  "korean": strip["korean"], "width_px": strip["width_px"]})

    # --- patch the whole-word hit onomatopoeia in the sprite bank (HITFX-001) ---
    hitfx_cfg = json.loads((project / "workspace/config/hit_effects.json").read_text(encoding="utf-8"))
    hitfx_records = []
    for effect in hitfx_cfg["effects"]:
        offset = int(effect["disc_offset"], 16)
        length = (effect["w"] // 2) * effect["h"]
        original = bytes(hagecho[offset:offset + length])
        if hashlib.sha256(original).hexdigest() != effect["sha256"]:
            raise ValueError(f"hit effect precondition mismatch: {effect['name']}")
        encoded = encode_effect_4bpp(effect["korean"], effect["w"], effect["h"])
        if len(encoded) != length:
            raise ValueError(f"encoded hit effect wrong size for {effect['korean']}")
        hagecho[offset:offset + length] = encoded
        hitfx_records.append({"offset": effect["disc_offset"], "japanese": effect["japanese"],
                              "korean": effect["korean"], "box": [effect["w"], effect["h"]]})

    # --- translate the big sprite-font cells (BIGFONT-KOREAN-001) ---
    bigfont_records = []
    if not args.bigfont_markers:
        bigfont_cfg = json.loads((project / "workspace/config/bigfont_korean.json").read_text(encoding="utf-8"))
        for cell in bigfont_cfg["cells"]:
            if cell["action"] == "keep":
                continue
            offset = int(cell["disc_offset"], 16)
            length = (cell["w"] // 2) * cell["h"]
            original = bytes(hagecho[offset:offset + length])
            if hashlib.sha256(original).hexdigest() != cell["sha256"]:
                raise ValueError(f"big-font cell precondition mismatch: {cell['index']} ({cell['glyph']})")
            encoded = encode_bigfont_4bpp(cell["korean"] or "", cell["w"], cell["h"])
            if len(encoded) != length:
                raise ValueError(f"encoded big-font cell wrong size: {cell['index']}")
            hagecho[offset:offset + length] = encoded
            bigfont_records.append({"index": cell["index"], "glyph": cell["glyph"],
                                    "korean": cell["korean"], "action": cell["action"]})

    # --- diagnostic: stamp the big-font cells with their own index (BIGFONT-WORDS-002) ---
    marker_records = []
    if args.bigfont_markers:
        bigfont = json.loads((project / "work/analysis/bigfont_sprite_001.json").read_text(encoding="utf-8"))
        for cell in bigfont["cells"]:
            offset = int(cell["disc_offset"], 16)
            length = (cell["w"] // 2) * cell["h"]
            encoded = encode_marker_4bpp(cell["index"], cell["w"], cell["h"])
            if len(encoded) != length:
                raise ValueError(f"marker wrong size for cell {cell['index']}")
            hagecho[offset:offset + length] = encoded
            marker_records.append({"index": cell["index"], "glyph": cell["glyph"],
                                   "disc_offset": cell["disc_offset"]})

    cheat_records = []
    if not args.no_cheats:
        # --- auto-enable the built-in title cheats (all characters + special options) ---
        cheat_cfg = json.loads((project / "workspace/config/cheat_unlock.json").read_text(encoding="utf-8"))
        for patch in cheat_cfg["patches"]:
            offset = patch["hagecho_offset"]
            expected = int(patch["original_word"], 16)
            if struct.unpack_from("<I", hagecho, offset)[0] != expected:
                raise ValueError(f"cheat patch precondition mismatch at {patch['runtime']}")
            struct.pack_into("<I", hagecho, offset, int(patch["patched_word"], 16))
            cheat_records.append({"runtime": patch["runtime"], "description": patch["description"],
                                  "asm": f"{patch['original_asm']} -> {patch['patched_asm']}"})

    # --- write the disc ---
    original_ara = ara_path.read_bytes()
    original_hagecho = hagecho_path.read_bytes()
    with source_bin.open("rb") as disc:
        disc_ara, ara_forms, _ = read_extent(disc, 63, len(original_ara))
        disc_hagecho, hagecho_forms, _ = read_extent(disc, 498, len(original_hagecho))
    if ara_forms != {"form1"} or disc_ara != original_ara or hagecho_forms != {"form1"} or disc_hagecho != original_hagecho:
        raise ValueError("disc extents do not equal extracted baselines")

    if args.bigfont_markers:
        build_id, build_tag = "hangul_atlas_001_markers", "HANGUL-ATLAS-001-MARKERS"
    elif args.no_cheats:
        build_id, build_tag = "hangul_atlas_no_cheat_001", "HANGUL-ATLAS-NO-CHEAT-001"
    else:
        build_id, build_tag = "hangul_atlas_001", "HANGUL-ATLAS-001"
    output_dir = project / "build" / build_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_bin = output_dir / f"Rakugaki Showtime (Japan) [{build_tag}].bin"
    output_cue = output_bin.with_suffix(".cue")
    temporary = output_bin.with_suffix(".bin.tmp")
    if temporary.exists():
        temporary.unlink()
    shutil.copyfile(source_bin, temporary)
    temporary.replace(output_bin)
    sectors = []
    sectors += patch_extent_on_disc(source_bin, output_bin, 63, original_ara, bytes(ara), "ARA decoder, builder, strings")
    sectors += patch_extent_on_disc(source_bin, output_bin, 498, original_hagecho, bytes(hagecho), "HAGECHO atlas and strings")
    with output_bin.open("rb") as built:
        rebuilt_ara, _, _ = read_extent(built, 63, len(original_ara))
        rebuilt_hagecho, _, _ = read_extent(built, 498, len(original_hagecho))
    if rebuilt_ara != bytes(ara) or rebuilt_hagecho != bytes(hagecho):
        raise ValueError("rebuilt extents differ from plan")
    if output_bin.stat().st_size != source_bin.stat().st_size:
        raise ValueError("output BIN size changed")
    cue_text, count = re.subn(r'(?im)^FILE\s+"[^"]+"\s+BINARY$', f'FILE "{output_bin.name}" BINARY',
                              source_cue.read_text(encoding="ascii"))
    if count != 1:
        raise ValueError("expected exactly one CUE FILE line")
    output_cue.write_text(cue_text, encoding="ascii", newline="")

    preview_dir = project / "work/graphics" / build_id
    preview_dir.mkdir(parents=True, exist_ok=True)
    for palette in range(4):
        image = Image.new("L", (256, 256), 0)
        pixels = image.load()
        for cell, grid in cells.items():
            cx, cy = (cell & 0xF) * 16, (cell >> 4) * 16
            for y in range(16):
                for x in range(16):
                    if grid[y][x] >> palette & 1:
                        pixels[cx + x, cy + y] = 255
        image.save(preview_dir / f"atlas_plane{palette}.png")

    report = {
        "schema_version": 1,
        "observation_id": build_tag,
        "status": "STATIC_BUILD_PASS_RUNTIME_PENDING",
        "design": {
            "packing": "two glyphs per 4bpp cell; planes 0,1 = bank 0 glyph, planes 2,3 = bank 1 glyph",
            "palette_rule": "clut palette = (bank << 1) | ((global_counter >> 2) & 1)",
            "wobble_frames": 2,
            "atlas_vram": [960, 256, 64, 256],
            "capacity_cells": 255,
            "capacity_glyphs": capacity,
            "second_vram_page_required": False,
        },
        "encoding": {"code_base": f"0x{CODE_BASE:04X}", "blank_cell": f"0x{BLANK_CELL:02X}",
                     "space_code": f"0x{space_code:04X}",
                     "decoder": {"runtime": f"0x{DECODER_RUNTIME:08X}", "instructions": DECODER_EXPECTED},
                     "builder": {"runtime": f"0x{BUILDER_RUNTIME:08X}", "instructions": BUILDER_CODE}},
        "glyphs": {"unique": len(glyphs), "cells_used": len(cells),
                   "assignment": {ch: assignment[ch]["code"] for ch in glyphs}},
        "menu_strips": {"count": len(menu_records), "records": menu_records},
        "charselect_labels": {"count": len(charselect_records), "records": charselect_records},
        "banner_words": {"count": len(banner_records), "records": banner_records},
        "hit_effects": {"count": len(hitfx_records), "records": hitfx_records},
        "staffroll_strips": {"count": len(staffroll_records), "records": staffroll_records},
        "bigfont_markers": {"count": len(marker_records), "records": marker_records},
        "bigfont_korean": {"count": len(bigfont_records), "records": bigfont_records},
        "cheat_unlock": {"enabled": not args.no_cheats, "count": len(cheat_records),
                         "records": cheat_records},
        "strings": {"rows_total": len(rows), "applied": len(applied), "deferred": len(deferred),
                    "deferred_rows": [{"id": row["id"], "reason": row["reason"],
                                       "original_text": row["original_text"],
                                       "translation": row["translation"]} for row in deferred]},
        "expected_writes": {"sector_count": len(sectors), "sectors": sectors},
        "logical_changes": {
            "ARA.BIN": {"sha256": sha256_bytes(bytes(ara)),
                        "changed_ranges": len(changed_ranges(original_ara, bytes(ara)))},
            "HAGECHO.BIN": {"sha256": sha256_bytes(bytes(hagecho)),
                            "changed_ranges": len(changed_ranges(original_hagecho, bytes(hagecho)))},
        },
        "output": {"bin": output_bin.relative_to(project).as_posix(), "bin_sha256": sha256_file(output_bin),
                   "cue": output_cue.relative_to(project).as_posix(), "cue_sha256": sha256_file(output_cue),
                   "size": output_bin.stat().st_size},
        "runtime_gate": "Fresh boot: title prompt, menus, options and in-game text must render Korean with two-frame wobble; verify the atlas readback at (960,256) equals the built payload. "
                        + ("Automatic character/special-option unlocks must be active."
                           if not args.no_cheats else
                           "Original character and special-option unlock progression must remain active."),
    }
    report_path = project / "work/analysis" / f"{build_id}_build.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "glyphs": len(glyphs), "cells": len(cells),
                      "applied": len(applied), "deferred": len(deferred),
                      "sectors": len(sectors), "output": report["output"]["bin_sha256"][:16]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
