#!/usr/bin/env python3
"""Build the (448,0)-resident two-bank font E2E PoC with Expected Writes.

FONT-TWO-BANK-POC-002 differs from PoC-001 in three ways:
1. bank 1 lives on the audited empty page (448,0) (tpage 0x07), so the shared
   builder patch derives the tpage with an XOR-0x18 mask instead of XOR-1 and
   re-implements the whole 27-instruction tpage/U/V window in place;
2. the bank-1 glyphs are delivered by a new mini-TIM appended in block 0 tail
   slack and registered by relocating the module asset directory there (the
   block 0 header pointer at +0x8 is repointed);
3. glyph pixels come from Room No.703.ttf as four jittered 1-bit wobble planes
   matching the stock atlas bitplane/palette scheme.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import struct
import sys
from pathlib import Path

from PIL import Image, ImageFont

from build_two_bank_poc import (
    assemble,
    changed_ranges,
    disassemble,
    patch_extent_on_disc,
    render_mask,
    sha256_bytes,
    sha256_file,
)
from psx_disc import read_extent


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def render_planes(character: str, font: ImageFont.FreeTypeFont, threshold: int,
                  offsets: list[list[int]]) -> list[Image.Image]:
    base = render_mask(character, font, threshold)
    pixels = base.load()
    on = [(x, y) for y in range(16) for x in range(16) if pixels[x, y]]
    planes = []
    for dx, dy in offsets:
        plane = Image.new("L", (16, 16), 0)
        target = plane.load()
        for x, y in on:
            nx, ny = x + dx, y + dy
            if not (0 <= nx < 16 and 0 <= ny < 16):
                raise ValueError(f"plane offset ({dx},{dy}) pushes {character!r} outside its 16x16 cell")
            target[nx, ny] = 255
        planes.append(plane)
    return planes


def compose_cell_nibbles(planes: list[Image.Image]) -> list[list[int]]:
    loaded = [plane.load() for plane in planes]
    return [
        [sum((1 << k) if loaded[k][x, y] else 0 for k in range(len(loaded))) for x in range(16)]
        for y in range(16)
    ]


def build_mini_tim(config: dict, hagecho: bytes, cells: dict[int, list[list[int]]]) -> bytes:
    mini = config["bank_1"]["mini_tim"]
    clut_offset = mini["clut_source_hagecho_offset"]
    clut_payload = hagecho[clut_offset : clut_offset + 256]
    if sha256_bytes(clut_payload) != mini["clut_payload_sha256"]:
        raise ValueError("font CLUT payload hash mismatch")
    dest_x, dest_y = mini["image_dest_words"]
    width_words, height = mini["image_size_words"]
    width_px = width_words * 4
    image_payload = bytearray(width_words * 2 * height)
    ordered = sorted(cells)
    if len(ordered) * 16 != width_px:
        raise ValueError("cell count does not match mini-TIM width")
    for slot, cell in enumerate(ordered):
        nibbles = cells[cell]
        for y in range(16):
            for x in range(16):
                px = slot * 16 + x
                offset = y * width_words * 2 + px // 2
                value = nibbles[y][x]
                if not 0 <= value <= 0xF:
                    raise ValueError("nibble out of range")
                old = image_payload[offset]
                image_payload[offset] = (old & 0x0F) | (value << 4) if px & 1 else (old & 0xF0) | value
    cx, cy, cw, ch = mini["clut_dest_words"]
    tim = bytearray()
    tim += struct.pack("<II", 0x10, 0x08)
    tim += struct.pack("<IHHHH", 12 + len(clut_payload), cx, cy, cw, ch) + clut_payload
    tim += struct.pack("<IHHHH", 12 + len(image_payload), dest_x, dest_y, width_words, height) + bytes(image_payload)
    return bytes(tim)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    project = args.project.resolve()
    config = json.loads((project / "workspace/config/font_two_bank_poc_002.json").read_text(encoding="utf-8"))
    manifest = json.loads((project / "workspace/config/source_manifest.json").read_text(encoding="utf-8"))
    source_bin = project / manifest["disc"]["bin"]
    source_cue = project / manifest["disc"]["cue"]
    ara_path = project / "extracted/ARA.BIN"
    hagecho_path = project / "extracted/HAGECHO.BIN"
    font_path = Path(config["source_font"]["path"])
    for path, expected in [
        (source_bin, manifest["disc"]["bin_sha256"]),
        (source_cue, manifest["disc"]["cue_sha256"]),
        (ara_path, manifest["expected_files"]["ARA.BIN"]["sha256"]),
        (hagecho_path, manifest["expected_files"]["HAGECHO.BIN"]["sha256"]),
        (font_path, config["source_font"]["expected_sha256"]),
    ]:
        if sha256_file(path) != expected:
            raise ValueError(f"source hash mismatch: {path}")

    ara = ara_path.read_bytes()
    hagecho = hagecho_path.read_bytes()
    with source_bin.open("rb") as disc:
        disc_ara, ara_forms, _ = read_extent(disc, 63, len(ara))
        disc_hagecho, hagecho_forms, _ = read_extent(disc, 498, len(hagecho))
    if ara_forms != {"form1"} or disc_ara != ara or hagecho_forms != {"form1"} or disc_hagecho != hagecho:
        raise ValueError("disc extents do not equal extracted baselines")

    # --- ARA: widened bank-aware builder patch ---
    modified_ara = bytearray(ara)
    builder = config["builder_patch"]
    builder_offset = builder["ara_offset"]
    expected_builder = bytes.fromhex(builder["expected_hex"])
    if modified_ara[builder_offset : builder_offset + len(expected_builder)] != expected_builder:
        raise ValueError("builder patch precondition mismatch")
    builder_runtime = int(builder["runtime_start"], 16)
    builder_patch = assemble(builder["instructions"], builder_runtime)
    if len(builder_patch) != len(expected_builder):
        raise ValueError(f"builder patch length mismatch: {len(builder_patch)} != {len(expected_builder)}")
    roundtrip = disassemble(builder_patch, builder_runtime)
    if roundtrip != builder["instructions"]:
        raise ValueError(f"builder patch assembly round-trip mismatch: {roundtrip}")
    modified_ara[builder_offset : builder_offset + len(builder_patch)] = builder_patch

    # --- ARA: decoder handler returns for the two PoC tokens ---
    decoder_records: list[dict] = []
    for glyph in config["glyphs"]:
        offset = glyph["decoder_handler_ara_offset"] + 4
        runtime = int(glyph["decoder_handler_runtime"], 16) + 4
        expected = assemble([f"addiu $v0, $zero, 0x{glyph['expected_original_return']:x}"], runtime)
        if modified_ara[offset : offset + 4] != expected:
            raise ValueError(f"decoder handler precondition mismatch for {glyph['character']}")
        replacement = assemble([f"addiu $v0, $zero, 0x{glyph['return_value']:x}"], runtime)
        modified_ara[offset : offset + 4] = replacement
        decoder_records.append({
            "character": glyph["character"],
            "token_hex": glyph["token_hex"],
            "handler_runtime": glyph["decoder_handler_runtime"],
            "return_value": f"0x{glyph['return_value']:03X}",
            "disassembly": disassemble(replacement, runtime)[0],
        })

    # --- HAGECHO block 0: title tokens ---
    modified_hagecho = bytearray(hagecho)
    title = config["title_string"]
    title_offset = title["hagecho_offset"]
    full_original = bytes.fromhex(title["full_original_hex"])
    if modified_hagecho[title_offset : title_offset + len(full_original)] != full_original:
        raise ValueError("title string precondition mismatch")
    expected_prefix = bytes.fromhex(title["expected_prefix_hex"])
    replacement_prefix = bytes.fromhex(title["replacement_prefix_hex"])
    if modified_hagecho[title_offset : title_offset + len(expected_prefix)] != expected_prefix:
        raise ValueError("title prefix precondition mismatch")
    modified_hagecho[title_offset : title_offset + len(replacement_prefix)] = replacement_prefix

    # --- HAGECHO block 0: glyph planes -> mini-TIM in tail slack ---
    font = ImageFont.truetype(str(font_path), config["source_font"]["size_px"])
    offsets = config["source_font"]["plane_offsets"]
    threshold = config["source_font"]["threshold"]
    cells: dict[int, list[list[int]]] = {}
    plane_previews: dict[str, list[Image.Image]] = {}
    for glyph in config["glyphs"]:
        planes = render_planes(glyph["character"], font, threshold, offsets)
        cells[glyph["cell"]] = compose_cell_nibbles(planes)
        plane_previews[glyph["character"]] = planes
    mini_tim = build_mini_tim(config, hagecho, cells)

    directory = config["block0_directory"]
    base = int(directory["module_runtime_base"], 16)
    header_off = directory["header_pointer_offset"]
    expected_pointer = int(directory["expected_header_pointer"], 16)
    if struct.unpack_from("<I", modified_hagecho, header_off)[0] != expected_pointer:
        raise ValueError("block 0 header directory pointer precondition mismatch")
    slack = bytes(modified_hagecho[directory["slack_start"] : directory["slack_end"]])
    if any(slack):
        raise ValueError("block 0 tail slack is not zero")
    table_source = bytes.fromhex(directory["table_source_hex"])
    src_off = directory["table_source_offset"]
    if modified_hagecho[src_off : src_off + len(table_source)] != table_source:
        raise ValueError("asset directory source precondition mismatch")
    entries = [struct.unpack_from("<II", table_source, i) for i in range(0, len(table_source), 8)]
    if entries[-1][0] != int(directory["terminator_tag"], 16) or entries[-1][1] != 0:
        raise ValueError("asset directory terminator mismatch")
    mini_tim_offset = directory["mini_tim_offset"]
    new_entries = entries[:-1] + [(int(directory["tim_entry_tag"], 16), base + mini_tim_offset)] + [entries[-1]]
    new_table = b"".join(struct.pack("<II", tag, ptr) for tag, ptr in new_entries)
    new_table_offset = directory["new_table_offset"]
    if new_table_offset + len(new_table) > mini_tim_offset:
        raise ValueError("relocated table overlaps the mini-TIM")
    if mini_tim_offset + len(mini_tim) > directory["slack_end"]:
        raise ValueError("mini-TIM exceeds block 0 tail slack")
    modified_hagecho[new_table_offset : new_table_offset + len(new_table)] = new_table
    modified_hagecho[mini_tim_offset : mini_tim_offset + len(mini_tim)] = mini_tim
    struct.pack_into("<I", modified_hagecho, header_off, base + new_table_offset)

    # --- write disc ---
    output_dir = project / "build/font_two_bank_poc_002"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_bin = output_dir / "Rakugaki Showtime (Japan) [FONT-TWO-BANK-POC-002].bin"
    output_cue = output_bin.with_suffix(".cue")
    temporary = output_bin.with_suffix(".bin.tmp")
    if temporary.exists():
        temporary.unlink()
    shutil.copyfile(source_bin, temporary)
    temporary.replace(output_bin)
    sector_records = []
    sector_records.extend(patch_extent_on_disc(source_bin, output_bin, 63, ara, bytes(modified_ara),
                                               "ARA decoder handlers and widened bank-aware builder"))
    sector_records.extend(patch_extent_on_disc(source_bin, output_bin, 498, hagecho, bytes(modified_hagecho),
                                               "HAGECHO block 0 title tokens, relocated asset directory, bank-1 mini-TIM"))

    with output_bin.open("rb") as built:
        rebuilt_ara, rebuilt_ara_forms, _ = read_extent(built, 63, len(ara))
        rebuilt_hagecho, rebuilt_hagecho_forms, _ = read_extent(built, 498, len(hagecho))
    if rebuilt_ara_forms != {"form1"} or rebuilt_ara != bytes(modified_ara):
        raise ValueError("rebuilt ARA differs from plan")
    if rebuilt_hagecho_forms != {"form1"} or rebuilt_hagecho != bytes(modified_hagecho):
        raise ValueError("rebuilt HAGECHO differs from plan")
    if output_bin.stat().st_size != source_bin.stat().st_size:
        raise ValueError("output BIN size changed")
    cue_text = source_cue.read_text(encoding="ascii")
    cue_text, count = re.subn(r'(?im)^FILE\s+"[^"]+"\s+BINARY$', f'FILE "{output_bin.name}" BINARY', cue_text)
    if count != 1:
        raise ValueError("expected exactly one CUE FILE line")
    output_cue.write_text(cue_text, encoding="ascii", newline="")

    # --- previews ---
    preview_dir = project / "work/graphics/font_two_bank_poc_002"
    preview_dir.mkdir(parents=True, exist_ok=True)
    columns = len(offsets) + 1
    preview = Image.new("L", (columns * 96, len(plane_previews) * 96), 32)
    for row, (character, planes) in enumerate(plane_previews.items()):
        composite = Image.new("L", (16, 16), 0)
        composite_px = composite.load()
        for plane in planes:
            plane_px = plane.load()
            for y in range(16):
                for x in range(16):
                    if plane_px[x, y]:
                        composite_px[x, y] = min(255, composite_px[x, y] + 60)
        for col, image in enumerate(planes + [composite]):
            preview.paste(image.resize((96, 96), Image.Resampling.NEAREST), (col * 96, row * 96))
    preview_path = preview_dir / "bank1_planes.png"
    preview.save(preview_path)

    ara_ranges = changed_ranges(ara, bytes(modified_ara))
    hagecho_ranges = changed_ranges(hagecho, bytes(modified_hagecho))
    mini = config["bank_1"]["mini_tim"]
    report = {
        "schema_version": 1,
        "observation_id": config["observation_id"],
        "status": "STATIC_BUILD_PASS_RUNTIME_PENDING",
        "scope": config["scope"],
        "source": {
            "bin_sha256": manifest["disc"]["bin_sha256"],
            "ara_sha256": manifest["expected_files"]["ARA.BIN"]["sha256"],
            "hagecho_sha256": manifest["expected_files"]["HAGECHO.BIN"]["sha256"],
            "font": {"path": str(font_path), "sha256": config["source_font"]["expected_sha256"]},
        },
        "abi": {
            "decoder_return": "bit 8 = bank, bits 7..0 = cell",
            "bank_0_tpage": f"0x{config['bank_0']['tpage']:02X}",
            "bank_1_tpage": f"0x{config['bank_1']['tpage']:02X}",
            "tpage_derivation": "tpage = 0x1F ^ (0x18 if bank bit else 0), via subu/andi mask",
            "builder_runtime": builder["runtime_start"],
            "builder_disassembly": disassemble(builder_patch, builder_runtime),
        },
        "decoder_handlers": decoder_records,
        "upload_path": {
            "mechanism": "block 0 asset directory relocated to tail slack with one appended 0xFFFF0001 TIM entry",
            "header_pointer": {"offset": header_off,
                               "old": directory["expected_header_pointer"],
                               "new": f"0x{base + new_table_offset:08X}"},
            "new_table_offset": new_table_offset,
            "entry_count": len(new_entries),
            "mini_tim": {
                "hagecho_offset": mini_tim_offset,
                "runtime": f"0x{base + mini_tim_offset:08X}",
                "size": len(mini_tim),
                "sha256": sha256_bytes(mini_tim),
                "image_dest_words": mini["image_dest_words"],
                "image_size_words": mini["image_size_words"],
                "clut_reupload": "byte-identical copy of the stock font CLUT block",
            },
        },
        "glyph_cells": {f"0x{cell:02X}": {"nonzero_pixels": sum(1 for row in nib for v in row if v)}
                        for cell, nib in cells.items()},
        "title": {
            "hagecho_offset": title_offset,
            "original_prefix_hex": title["expected_prefix_hex"],
            "patched_prefix_hex": title["replacement_prefix_hex"],
            "expected_display_prefix": "한글",
        },
        "logical_changes": {
            "ARA.BIN": {
                "range_count": len(ara_ranges),
                "changed_byte_count": sum(end - start for start, end in ara_ranges),
                "ranges": [{"offset": start, "length": end - start} for start, end in ara_ranges],
                "sha256": sha256_bytes(bytes(modified_ara)),
            },
            "HAGECHO.BIN": {
                "range_count": len(hagecho_ranges),
                "changed_byte_count": sum(end - start for start, end in hagecho_ranges),
                "ranges": [{"offset": start, "length": end - start} for start, end in hagecho_ranges],
                "sha256": sha256_bytes(bytes(modified_hagecho)),
            },
        },
        "expected_writes": {
            "coordinate": "raw BIN LBA, 2352-byte sectors",
            "sector_count": len(sector_records),
            "sectors": sector_records,
        },
        "output": {
            "bin": output_bin.relative_to(project).as_posix(),
            "bin_sha256": sha256_file(output_bin),
            "cue": output_cue.relative_to(project).as_posix(),
            "cue_sha256": sha256_file(output_cue),
            "size": output_bin.stat().st_size,
        },
        "checks": {
            "source_hashes": "PASS",
            "disc_extents_equal_extracted": "PASS",
            "machine_code_assembly_disassembly": "PASS",
            "expected_write_preconditions": "PASS",
            "modified_sector_edc_ecc": "PASS",
            "rebuilt_extents_equal_plan": "PASS",
            "output_size_preserved": "PASS",
        },
        "preview": preview_path.relative_to(project).as_posix(),
        "runtime_gate": (
            "Fresh boot the generated CUE; verify via GDB readback that (504,208,8,16) equals the mini-TIM image "
            "payload, that the moving title prompt begins with 한글 with motion preserved, and that Mode Select is "
            "reachable. The upload proves the relocated asset directory is honored by the loader."
        ),
    }
    report_path = project / "work/analysis/font_two_bank_poc_002_build.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": report["output"],
                      "sectors": len(sector_records),
                      "upload_path": report["upload_path"]["header_pointer"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
