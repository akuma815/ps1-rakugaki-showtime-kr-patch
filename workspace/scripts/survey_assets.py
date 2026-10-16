#!/usr/bin/env python3
"""Verify the source disc and inventory Rakugaki Showtime's main asset containers."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from collections import Counter
from pathlib import Path

from psx_disc import RAW_SECTOR_SIZE, extract_files, inspect_iso, read_raw_sector
from psx_tim import scan_tims


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_equal(label: str, actual, expected) -> None:
    if actual != expected:
        raise ValueError(f"{label}: expected {expected!r}, got {actual!r}")


def validate_raw_track(image: Path, expected_sectors: int) -> dict:
    forms = Counter()
    with image.open("rb") as track:
        for lba in range(expected_sectors):
            raw = read_raw_sector(track, lba)
            forms["form2" if raw[18] & 0x20 else "form1"] += 1
    return {"sector_count": expected_sectors, "forms": dict(sorted(forms.items()))}


def parse_hagecho_blocks(do_data: bytes, hagecho_data: bytes) -> list[dict]:
    if len(do_data) % 4:
        raise ValueError("DO.BIN size is not divisible by four")
    offsets = list(struct.unpack(f"<{len(do_data) // 4}I", do_data))
    if len(offsets) < 2 or offsets[0] != 0 or offsets[-1] != len(hagecho_data):
        raise ValueError("DO.BIN endpoints do not match HAGECHO.BIN")
    if any(left >= right for left, right in zip(offsets, offsets[1:])):
        raise ValueError("DO.BIN offsets are not strictly increasing")
    blocks = []
    for index, (start, end) in enumerate(zip(offsets, offsets[1:])):
        data = hagecho_data[start:end]
        blocks.append({
            "index": index,
            "start": start,
            "end": end,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    return blocks


def tim_records(source: str, data: bytes, base_offset: int = 0, block_index: int | None = None) -> list[dict]:
    output = []
    for index, tim in enumerate(scan_tims(data)):
        raw = data[tim["offset"] : tim["offset"] + tim["total_size"]]
        record = {
            "source": source,
            "index_in_source": index,
            "source_offset": tim["offset"],
            "container_offset": base_offset + tim["offset"],
            "bpp": tim["bpp"],
            "width": tim["pixel_width"],
            "height": tim["pixel_height"],
            "total_size": tim["total_size"],
            "sha256": hashlib.sha256(raw).hexdigest(),
            "image_vram": {"x": tim["image"]["x"], "y": tim["image"]["y"]},
        }
        if block_index is not None:
            record["block_index"] = block_index
        if tim["clut"] is not None:
            record["clut"] = {
                "x": tim["clut"]["x"],
                "y": tim["clut"]["y"],
                "colors": tim["clut"]["width_words"] * tim["clut"]["height"],
            }
        output.append(record)
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--skip-full-sector-check", action="store_true")
    args = parser.parse_args()
    project = args.project.resolve()
    config = json.loads((project / "workspace/config/source_manifest.json").read_text(encoding="utf-8"))
    disc_config = config["disc"]
    image = project / disc_config["bin"]
    cue = project / disc_config["cue"]

    require_equal("BIN size", image.stat().st_size, disc_config["bin_size"])
    require_equal("BIN SHA-256", sha256_file(image), disc_config["bin_sha256"])
    require_equal("CUE SHA-256", sha256_file(cue), disc_config["cue_sha256"])
    require_equal("raw-sector divisibility", image.stat().st_size % RAW_SECTOR_SIZE, 0)

    raw_validation = None
    if not args.skip_full_sector_check:
        raw_validation = validate_raw_track(image, disc_config["sector_count"])
    disc = inspect_iso(image)
    manifest_path = project / "work/analysis/disc_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(disc, ensure_ascii=False, indent=2), encoding="utf-8")

    names = set(config["expected_files"])
    extracted = extract_files(image, disc["entries"], names, project / "extracted")
    extracted_map = {item["path"]: item for item in extracted}
    for name, expected in config["expected_files"].items():
        require_equal(f"{name} size", extracted_map[name]["size"], expected["size"])
        require_equal(f"{name} SHA-256", extracted_map[name]["sha256"], expected["sha256"])

    do_data = (project / "extracted/DO.BIN").read_bytes()
    hagecho_data = (project / "extracted/HAGECHO.BIN").read_bytes()
    blocks = parse_hagecho_blocks(do_data, hagecho_data)
    block_dir = project / "extracted/hagecho_blocks"
    block_dir.mkdir(parents=True, exist_ok=True)
    for block in blocks:
        (block_dir / f"block_{block['index']:03d}.bin").write_bytes(hagecho_data[block["start"] : block["end"]])

    block_report = {
        "schema_version": 1,
        "do_bin_size": len(do_data),
        "offset_count": len(blocks) + 1,
        "hagecho_size": len(hagecho_data),
        "block_count": len(blocks),
        "blocks": blocks,
    }
    block_path = project / "work/analysis/hagecho_blocks.json"
    block_path.write_text(json.dumps(block_report, ensure_ascii=False, indent=2), encoding="utf-8")

    records = tim_records("ARA.BIN", (project / "extracted/ARA.BIN").read_bytes())
    for block in blocks:
        start, end = block["start"], block["end"]
        records.extend(tim_records(
            f"HAGECHO/block_{block['index']:03d}", hagecho_data[start:end], start, block["index"]
        ))
    dimensions = Counter(f"{item['width']}x{item['height']}x{item['bpp']}" for item in records)
    tim_report = {
        "schema_version": 1,
        "valid_tim_count": len(records),
        "source_counts": dict(sorted(Counter(item["source"].split("/")[0] for item in records).items())),
        "dimension_counts": dict(dimensions.most_common()),
        "records": records,
    }
    tim_path = project / "work/analysis/tim_inventory.json"
    tim_path.write_text(json.dumps(tim_report, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = {
        "source_verified": True,
        "raw_track_validation": raw_validation,
        "disc_file_count": sum(not item["is_dir"] for item in disc["entries"]),
        "extracted": extracted,
        "hagecho_block_count": len(blocks),
        "valid_tim_count": len(records),
        "outputs": [manifest_path.as_posix(), block_path.as_posix(), tim_path.as_posix()],
    }
    summary_path = project / "work/analysis/survey_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
