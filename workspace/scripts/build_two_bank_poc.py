#!/usr/bin/env python3
"""Build the title-only static two-bank font ABI PoC with Expected Writes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

from capstone import CS_ARCH_MIPS, CS_MODE_LITTLE_ENDIAN, CS_MODE_MIPS32, Cs
from keystone import KS_ARCH_MIPS, KS_MODE_LITTLE_ENDIAN, KS_MODE_MIPS32, Ks
from PIL import Image, ImageDraw, ImageFont

from psx_disc import FORM1_DATA_OFFSET, FORM1_DATA_SIZE, RAW_SECTOR_SIZE, read_extent, read_raw_sector
from psx_edc_ecc import regenerate_mode2_form1, verify_mode2_form1
from psx_tim import decode_tim, parse_tim


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


ARA_RUNTIME_DELTA = 0x7FFD6000


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def changed_ranges(before: bytes, after: bytes) -> list[tuple[int, int]]:
    if len(before) != len(after):
        raise ValueError("range comparison requires equal lengths")
    result: list[tuple[int, int]] = []
    start: int | None = None
    for index, (old, new) in enumerate(zip(before, after, strict=True)):
        if old != new and start is None:
            start = index
        elif old == new and start is not None:
            result.append((start, index))
            start = None
    if start is not None:
        result.append((start, len(before)))
    return result


def assemble(instructions: list[str], address: int) -> bytes:
    assembler = Ks(KS_ARCH_MIPS, KS_MODE_MIPS32 + KS_MODE_LITTLE_ENDIAN)
    encoding, _ = assembler.asm("\n".join(instructions), addr=address)
    return bytes(encoding)


def disassemble(data: bytes, address: int) -> list[str]:
    decoder = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 + CS_MODE_LITTLE_ENDIAN)
    return [f"{item.mnemonic} {item.op_str}".strip() for item in decoder.disasm(data, address)]


def render_mask(character: str, font: ImageFont.FreeTypeFont, threshold: int) -> Image.Image:
    bbox = font.getbbox(character)
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if width > 16 or height > 16:
        raise ValueError(f"glyph {character!r} does not fit 16x16: {width}x{height}")
    image = Image.new("L", (16, 16), 0)
    draw = ImageDraw.Draw(image)
    draw.text(((16 - width) // 2 - bbox[0], (16 - height) // 2 - bbox[1]), character, font=font, fill=255)
    return image.point(lambda value: 255 if value >= threshold else 0)


def get_4bpp_pixel(data: bytes | bytearray, image_offset: int, stride: int, x: int, y: int) -> int:
    value = data[image_offset + y * stride + x // 2]
    return (value >> 4) & 0xF if x & 1 else value & 0xF


def set_4bpp_pixel(data: bytearray, image_offset: int, stride: int, x: int, y: int, value: int) -> None:
    offset = image_offset + y * stride + x // 2
    old = data[offset]
    data[offset] = (old & 0x0F) | (value << 4) if x & 1 else (old & 0xF0) | value


def write_shared_page_cell(data: bytearray, tim: dict, cell: int, mask: Image.Image, background: int, ink: int) -> int:
    if cell >> 4 >= tim["pixel_height"] // 16:
        raise ValueError("PoC bank cell lies outside the shared TIM height")
    image_offset = tim["image"]["payload_offset"]
    stride = tim["image"]["width_words"] * 2
    cell_x, cell_y = (cell & 0xF) * 16, (cell >> 4) * 16
    for y in range(16):
        for x in range(16):
            actual = get_4bpp_pixel(data, image_offset, stride, cell_x + x, cell_y + y)
            if actual != background:
                raise ValueError(f"shared splash cell 0x{cell:02X} is not uniform background index {background}")
    pixels = mask.load()
    count = 0
    for y in range(16):
        for x in range(16):
            if pixels[x, y]:
                set_4bpp_pixel(data, image_offset, stride, cell_x + x, cell_y + y, ink)
                count += 1
    return count


def patch_extent_on_disc(source_bin: Path, output_bin: Path, lba_start: int, before: bytes, after: bytes, owner: str) -> list[dict]:
    ranges = changed_ranges(before, after)
    sector_indices = sorted({position // FORM1_DATA_SIZE for start, end in ranges for position in range(start, end)})
    records: list[dict] = []
    with source_bin.open("rb") as baseline, output_bin.open("r+b") as output:
        for sector_index in sector_indices:
            lba = lba_start + sector_index
            expected = read_raw_sector(baseline, lba)
            if not verify_mode2_form1(expected):
                raise ValueError(f"source sector integrity mismatch at LBA {lba}")
            final = bytearray(expected)
            logical_start = sector_index * FORM1_DATA_SIZE
            payload = after[logical_start : logical_start + FORM1_DATA_SIZE]
            final[FORM1_DATA_OFFSET : FORM1_DATA_OFFSET + len(payload)] = payload
            final_bytes = regenerate_mode2_form1(bytes(final))
            if not verify_mode2_form1(final_bytes):
                raise ValueError(f"regenerated sector integrity mismatch at LBA {lba}")
            output.seek(lba * RAW_SECTOR_SIZE)
            if output.read(RAW_SECTOR_SIZE) != expected:
                raise ValueError(f"Expected Write mismatch at LBA {lba}")
            output.seek(lba * RAW_SECTOR_SIZE)
            output.write(final_bytes)
            records.append({
                "owner": owner,
                "lba": lba,
                "expected_sha256": sha256_bytes(expected),
                "final_sha256": sha256_bytes(final_bytes),
                "raw_changed_byte_count": sum(a != b for a, b in zip(expected, final_bytes, strict=True)),
            })
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    project = args.project.resolve()
    config = json.loads((project / "workspace/config/font_two_bank_poc_001.json").read_text(encoding="utf-8"))
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

    modified_ara = bytearray(ara)
    builder = config["builder_patch"]
    builder_offset = builder["ara_offset"]
    expected_builder = bytes.fromhex(builder["expected_hex"])
    if modified_ara[builder_offset : builder_offset + len(expected_builder)] != expected_builder:
        raise ValueError("builder patch precondition mismatch")
    builder_runtime = int(builder["runtime_start"], 16)
    builder_patch = assemble(builder["instructions"], builder_runtime)
    if len(builder_patch) != len(expected_builder):
        raise ValueError("builder patch length changed")
    if disassemble(builder_patch, builder_runtime) != builder["instructions"]:
        raise ValueError("builder patch assembly round-trip mismatch")
    modified_ara[builder_offset : builder_offset + len(builder_patch)] = builder_patch

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

    bank = config["bank_1_poc"]
    tim = parse_tim(hagecho, bank["hagecho_offset"])
    if tim is None or tim["bpp"] != 4 or tim["pixel_width"] != 256 or tim["pixel_height"] != 224:
        raise ValueError("shared bank TIM structure mismatch")
    if sha256_bytes(hagecho[tim["offset"] : tim["offset"] + tim["total_size"]]) != bank["expected_tim_sha256"]:
        raise ValueError("shared bank TIM hash mismatch")
    font = ImageFont.truetype(str(font_path), config["source_font"]["size_px"])
    rendered: list[dict] = []
    for glyph in config["glyphs"]:
        mask = render_mask(glyph["character"], font, config["source_font"]["threshold"])
        ink_pixels = write_shared_page_cell(
            modified_hagecho,
            tim,
            glyph["cell"],
            mask,
            bank["background_index"],
            bank["ink_index"],
        )
        rendered.append({**glyph, "ink_pixels": ink_pixels})

    output_dir = project / "build/font_two_bank_poc_001"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_bin = output_dir / "Rakugaki Showtime (Japan) [FONT-TWO-BANK-POC-001].bin"
    output_cue = output_bin.with_suffix(".cue")
    temporary = output_bin.with_suffix(".bin.tmp")
    if temporary.exists():
        temporary.unlink()
    shutil.copyfile(source_bin, temporary)
    temporary.replace(output_bin)
    sector_records = []
    sector_records.extend(patch_extent_on_disc(source_bin, output_bin, 63, ara, bytes(modified_ara), "ARA decoder and bank-aware builder"))
    sector_records.extend(patch_extent_on_disc(source_bin, output_bin, 498, hagecho, bytes(modified_hagecho), "HAGECHO title tokens and PoC bank glyphs"))

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

    preview_dir = project / "work/graphics/font_two_bank_poc_001"
    preview_dir.mkdir(parents=True, exist_ok=True)
    before_image = decode_tim(hagecho, tim, 0)
    after_tim = parse_tim(bytes(modified_hagecho), bank["hagecho_offset"])
    assert after_tim is not None
    after_image = decode_tim(bytes(modified_hagecho), after_tim, 0)
    preview = Image.new("RGBA", (192, 384), (32, 32, 32, 255))
    for index, glyph in enumerate(config["glyphs"]):
        cell = glyph["cell"]
        box = ((cell & 15) * 16, (cell >> 4) * 16, (cell & 15) * 16 + 16, (cell >> 4) * 16 + 16)
        preview.paste(before_image.crop(box).resize((96, 96), Image.Resampling.NEAREST), (index * 96, 0))
        preview.paste(after_image.crop(box).resize((96, 96), Image.Resampling.NEAREST), (index * 96, 96))
        mask = render_mask(glyph["character"], font, config["source_font"]["threshold"])
        preview.paste(mask.convert("RGBA").resize((96, 96), Image.Resampling.NEAREST), (index * 96, 192))
    preview_path = preview_dir / "bank1_cells.png"
    preview.save(preview_path)

    ara_ranges = changed_ranges(ara, bytes(modified_ara))
    hagecho_ranges = changed_ranges(hagecho, bytes(modified_hagecho))
    report = {
        "schema_version": 1,
        "observation_id": config["observation_id"],
        "status": "STATIC_BUILD_PASS_RUNTIME_PENDING",
        "scope": "title-only bank propagation PoC; not a final atlas storage allocation",
        "source": {
            "bin": source_bin.relative_to(project).as_posix(),
            "bin_sha256": manifest["disc"]["bin_sha256"],
            "ara_sha256": manifest["expected_files"]["ARA.BIN"]["sha256"],
            "hagecho_sha256": manifest["expected_files"]["HAGECHO.BIN"]["sha256"],
        },
        "abi": {
            "decoder_return": "bit 8 = bank, bits 7..0 = cell",
            "bank_0_tpage": f"0x{config['bank_0']['tpage']:02X}",
            "bank_1_tpage": f"0x{bank['tpage']:02X}",
            "uv": "low cell byte retains existing 16x16 nibble mapping",
            "builder_runtime": builder["runtime_start"],
            "builder_disassembly": disassemble(builder_patch, builder_runtime),
        },
        "decoder_handlers": decoder_records,
        "glyphs": rendered,
        "title": {
            "hagecho_offset": title_offset,
            "original_prefix_hex": title["expected_prefix_hex"],
            "patched_prefix_hex": title["replacement_prefix_hex"],
            "expected_display_prefix": "한글",
        },
        "bank_1_asset": {
            **bank,
            "modified_cell_range": "0xDE-0xDF",
            "splash_side_effect": "subtle glyph-shaped palette-index changes in the lower-right corner are accepted only for this PoC",
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
        "runtime_gate": "Fresh boot the generated CUE; verify the moving title prompt begins with 한글, retains motion/orientation, and reaches Mode Select. Do not approve bank-1 persistence outside title from this build.",
    }
    report_path = project / "work/analysis/font_two_bank_poc_001_build.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": report["output"], "sectors": len(sector_records)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
