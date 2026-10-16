#!/usr/bin/env python3
"""Build FONT-VISIBILITY-001 through tracked raw-sector Expected Writes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from psx_disc import FORM1_DATA_OFFSET, FORM1_DATA_SIZE, RAW_SECTOR_SIZE, read_extent, read_raw_sector
from psx_edc_ecc import regenerate_mode2_form1, verify_mode2_form1
from psx_tim import decode_tim, parse_tim


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


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
    ranges: list[tuple[int, int]] = []
    start: int | None = None
    for index, (old, new) in enumerate(zip(before, after)):
        if old != new and start is None:
            start = index
        elif old == new and start is not None:
            ranges.append((start, index))
            start = None
    if start is not None:
        ranges.append((start, len(before)))
    return ranges


def render_cell(character: str, font: ImageFont.FreeTypeFont, threshold: int) -> Image.Image:
    bbox = font.getbbox(character)
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if width > 16 or height > 16:
        raise ValueError(f"glyph {character!r} does not fit 16x16: {width}x{height}")
    image = Image.new("L", (16, 16), 0)
    draw = ImageDraw.Draw(image)
    x = (16 - width) // 2 - bbox[0]
    y = (16 - height) // 2 - bbox[1]
    draw.text((x, y), character, font=font, fill=255)
    return image.point(lambda value: 255 if value >= threshold else 0)


def write_4bpp_cell(data: bytearray, tim: dict, cell: int, mask: Image.Image) -> None:
    if not 0 <= cell <= 0xFF or mask.size != (16, 16):
        raise ValueError("invalid cell or mask")
    image_offset = tim["image"]["payload_offset"]
    pixels = mask.load()
    cell_x = (cell & 0x0F) * 16
    cell_y = (cell >> 4) * 16
    for y in range(16):
        for x_pair in range(8):
            left = 0x0F if pixels[x_pair * 2, y] else 0
            right = 0x0F if pixels[x_pair * 2 + 1, y] else 0
            byte_offset = image_offset + (cell_y + y) * 128 + cell_x // 2 + x_pair
            data[byte_offset] = left | (right << 4)


def make_preview(before: bytes, after: bytes, tim_offset: int, glyphs: list[dict], target: Path) -> None:
    before_tim = parse_tim(before, tim_offset)
    after_tim = parse_tim(after, tim_offset)
    if before_tim is None or after_tim is None:
        raise ValueError("font TIM did not parse for preview")
    before_image = decode_tim(before, before_tim, 0)
    after_image = decode_tim(after, after_tim, 0)
    scale = 6
    canvas = Image.new("RGBA", (len(glyphs) * 16 * scale, 2 * 16 * scale), (32, 32, 32, 255))
    for index, item in enumerate(glyphs):
        cell = item["cell"]
        box = ((cell & 15) * 16, (cell >> 4) * 16, (cell & 15) * 16 + 16, (cell >> 4) * 16 + 16)
        canvas.paste(before_image.crop(box).resize((16 * scale, 16 * scale), Image.Resampling.NEAREST), (index * 16 * scale, 0))
        canvas.paste(after_image.crop(box).resize((16 * scale, 16 * scale), Image.Resampling.NEAREST), (index * 16 * scale, 16 * scale))
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--font", type=Path, default=Path(r"C:\Windows\Fonts\malgun.ttf"))
    args = parser.parse_args()

    project = args.project.resolve()
    source_manifest = json.loads((project / "workspace/config/source_manifest.json").read_text(encoding="utf-8"))
    config = json.loads((project / "workspace/config/font_poc_001.json").read_text(encoding="utf-8"))
    source_bin = project / source_manifest["disc"]["bin"]
    source_cue = project / source_manifest["disc"]["cue"]
    source_hagecho = project / "extracted/HAGECHO.BIN"
    output_dir = project / "build/font_poc_001"
    output_bin = output_dir / "Rakugaki Showtime (Japan) [FONT-POC-001].bin"
    output_cue = output_bin.with_suffix(".cue")
    report_path = project / "work/analysis/font_poc_001_build.json"
    preview_path = project / "work/graphics/font_poc_001/font_poc_cells.png"

    if sha256_file(source_bin) != source_manifest["disc"]["bin_sha256"]:
        raise ValueError("source BIN hash mismatch")
    if sha256_file(source_cue) != source_manifest["disc"]["cue_sha256"]:
        raise ValueError("source CUE hash mismatch")
    expected_hagecho_hash = source_manifest["expected_files"]["HAGECHO.BIN"]["sha256"]
    if sha256_file(source_hagecho) != expected_hagecho_hash:
        raise ValueError("extracted HAGECHO hash mismatch")
    if sha256_file(args.font) != config["source_font"]["expected_sha256"]:
        raise ValueError("PoC font hash mismatch")

    hagecho = source_hagecho.read_bytes()
    with source_bin.open("rb") as track:
        disc_hagecho, forms, lbas = read_extent(track, 498, len(hagecho))
    if forms != {"form1"} or disc_hagecho != hagecho:
        raise ValueError("disc HAGECHO extent does not match extracted baseline")

    tim_offset = config["font_tim"]["hagecho_offset"]
    tim = parse_tim(hagecho, tim_offset)
    if tim is None or tim["bpp"] != 4 or tim["pixel_width"] != 256 or tim["pixel_height"] != 256:
        raise ValueError("font TIM structure mismatch")
    if sha256_bytes(hagecho[tim_offset : tim_offset + tim["total_size"]]) != config["font_tim"]["expected_sha256"]:
        raise ValueError("font TIM hash mismatch")
    string = config["target_string"]
    expected_string = bytes.fromhex(string["expected_hex"])
    string_offset = string["hagecho_offset"]
    if hagecho[string_offset : string_offset + len(expected_string)] != expected_string:
        raise ValueError("title prompt expected bytes mismatch")

    font = ImageFont.truetype(str(args.font), config["source_font"]["size_px"])
    modified = bytearray(hagecho)
    rendered: list[dict] = []
    for item in config["glyphs"]:
        mask = render_cell(item["character"], font, config["source_font"]["threshold"])
        if not mask.getbbox():
            raise ValueError(f"font rendered empty glyph: {item['character']}")
        write_4bpp_cell(modified, tim, item["cell"], mask)
        rendered.append({**item, "ink_pixels": sum(1 for value in mask.get_flattened_data() if value)})
    modified_bytes = bytes(modified)
    logical_ranges = changed_ranges(hagecho, modified_bytes)
    if not logical_ranges:
        raise ValueError("PoC produced no HAGECHO changes")

    affected_sector_indices = sorted({offset // FORM1_DATA_SIZE for start, end in logical_ranges for offset in range(start, end)})
    planned_sectors: list[tuple[int, bytes, bytes]] = []
    sector_records: list[dict] = []
    with source_bin.open("rb") as track:
        for sector_index in affected_sector_indices:
            lba = 498 + sector_index
            before_sector = read_raw_sector(track, lba)
            if not verify_mode2_form1(before_sector):
                raise ValueError(f"original sector integrity mismatch at LBA {lba}")
            after_sector = bytearray(before_sector)
            logical_start = sector_index * FORM1_DATA_SIZE
            payload = modified_bytes[logical_start : logical_start + FORM1_DATA_SIZE]
            take = min(len(payload), FORM1_DATA_SIZE)
            after_sector[FORM1_DATA_OFFSET : FORM1_DATA_OFFSET + take] = payload
            final_sector = regenerate_mode2_form1(bytes(after_sector))
            if not verify_mode2_form1(final_sector):
                raise ValueError(f"regenerated sector integrity mismatch at LBA {lba}")
            planned_sectors.append((lba, before_sector, final_sector))
            sector_records.append({
                "lba": lba,
                "expected_sha256": sha256_bytes(before_sector),
                "final_sha256": sha256_bytes(final_sector),
                "raw_changed_byte_count": sum(a != b for a, b in zip(before_sector, final_sector)),
            })

    output_dir.mkdir(parents=True, exist_ok=True)
    temporary = output_bin.with_suffix(".bin.tmp")
    if temporary.exists():
        temporary.unlink()
    shutil.copyfile(source_bin, temporary)
    with temporary.open("r+b") as output:
        for lba, expected, final in planned_sectors:
            output.seek(lba * RAW_SECTOR_SIZE)
            actual = output.read(RAW_SECTOR_SIZE)
            if actual != expected:
                raise ValueError(f"Expected Write mismatch at LBA {lba}")
            output.seek(lba * RAW_SECTOR_SIZE)
            output.write(final)
    temporary.replace(output_bin)

    with output_bin.open("rb") as built:
        for lba, _, final in planned_sectors:
            built.seek(lba * RAW_SECTOR_SIZE)
            if built.read(RAW_SECTOR_SIZE) != final:
                raise ValueError(f"built sector mismatch at LBA {lba}")
        rebuilt_hagecho, rebuilt_forms, _ = read_extent(built, 498, len(hagecho))
    if rebuilt_forms != {"form1"} or rebuilt_hagecho != modified_bytes:
        raise ValueError("built HAGECHO does not match planned bytes")
    if output_bin.stat().st_size != source_bin.stat().st_size:
        raise ValueError("output BIN size changed")

    cue_text = source_cue.read_text(encoding="ascii")
    cue_text, substitutions = re.subn(r'(?im)^FILE\s+"[^"]+"\s+BINARY$', f'FILE "{output_bin.name}" BINARY', cue_text)
    if substitutions != 1:
        raise ValueError("expected exactly one CUE FILE line")
    output_cue.write_text(cue_text, encoding="ascii", newline="")
    make_preview(hagecho, modified_bytes, tim_offset, config["glyphs"], preview_path)

    report = {
        "schema_version": 1,
        "observation_id": config["observation_id"],
        "status": "STATIC_BUILD_PASS_RUNTIME_PENDING",
        "source": {
            "bin": source_bin.relative_to(project).as_posix(),
            "sha256": source_manifest["disc"]["bin_sha256"],
            "hagecho_sha256": expected_hagecho_hash,
        },
        "output": {
            "bin": output_bin.relative_to(project).as_posix(),
            "bin_sha256": sha256_file(output_bin),
            "cue": output_cue.relative_to(project).as_posix(),
            "cue_sha256": sha256_file(output_cue),
            "size": output_bin.stat().st_size,
        },
        "font": {
            "input_sha256": config["source_font"]["expected_sha256"],
            "size_px": config["source_font"]["size_px"],
            "threshold": config["source_font"]["threshold"],
        },
        "glyphs": rendered,
        "logical_hagecho_changes": {
            "range_count": len(logical_ranges),
            "changed_byte_count": sum(end - start for start, end in logical_ranges),
            "ranges": [{"offset": start, "length": end - start} for start, end in logical_ranges],
            "final_sha256": sha256_bytes(modified_bytes),
        },
        "expected_writes": {
            "coordinate": "raw BIN LBA, 2352-byte sectors",
            "owner": "FONT-VISIBILITY-001 font TIM plus derived Form 1 EDC/ECC",
            "sector_count": len(sector_records),
            "sectors": sector_records,
        },
        "checks": {
            "source_hashes": "PASS",
            "disc_extent_equals_extracted_hagecho": "PASS",
            "font_tim_expected_hash": "PASS",
            "title_prompt_expected_bytes": "PASS",
            "expected_write_preconditions": "PASS",
            "modified_sector_edc_ecc": "PASS",
            "untouched_sector_policy": "full image copied; only registered sector LBAs overwritten",
            "rebuilt_hagecho_equals_planned": "PASS",
            "output_size_preserved": "PASS",
        },
        "preview": preview_path.relative_to(project).as_posix(),
        "runtime_gate": "Boot the generated CUE from a fresh start, confirm both replacement glyphs in the title prompt, then enter Mode Select without new corruption.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": report["output"], "sectors": sector_records}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
