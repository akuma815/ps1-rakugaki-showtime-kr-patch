#!/usr/bin/env python3
"""Match DuckStation CPU-to-VRAM dumps to surveyed TIM payloads in PS1 RGB555 space."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from collections import Counter
from pathlib import Path

from PIL import Image

from psx_tim import parse_tim


def rgb555_bytes_from_image(image: Image.Image) -> bytes:
    output = bytearray()
    for red, green, blue in image.convert("RGB").get_flattened_data():
        value = round(red * 31 / 255) | (round(green * 31 / 255) << 5) | (round(blue * 31 / 255) << 10)
        output.extend(struct.pack("<H", value))
    return bytes(output)


def rgb555_without_stp(payload: bytes) -> bytes:
    values = struct.unpack(f"<{len(payload) // 2}H", payload)
    return struct.pack(f"<{len(values)}H", *(value & 0x7FFF for value in values))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    project = args.project.resolve()
    inventory = json.loads((project / "work/analysis/tim_inventory.json").read_text(encoding="utf-8"))
    hagecho = (project / "extracted/HAGECHO.BIN").read_bytes()

    tim_records = []
    tim_map: dict[tuple[int, int, str], list[dict]] = {}
    for catalog_index, record in enumerate(inventory["records"]):
        tim = parse_tim(hagecho, record["container_offset"])
        if tim is None:
            raise ValueError(f"TIM catalog record {catalog_index} no longer parses")
        meta = tim["image"]
        payload = hagecho[meta["payload_offset"] : meta["payload_offset"] + meta["payload_size"]]
        normalized_payload = rgb555_without_stp(payload)
        key = (meta["width_words"], meta["height"], hashlib.sha256(normalized_payload).hexdigest())
        item = {
            "catalog_index": catalog_index,
            "part": "image",
            "block_index": record.get("block_index"),
            "container_offset": record["container_offset"],
            "width_words": meta["width_words"],
            "height": meta["height"],
            "payload_sha256": key[2],
            "destination_from_tim": {"x_words": meta["x"], "y": meta["y"]},
        }
        tim_records.append(item)
        tim_map.setdefault(key, []).append(item)
        if tim["clut"] is not None:
            clut = tim["clut"]
            clut_payload = hagecho[clut["payload_offset"] : clut["payload_offset"] + clut["payload_size"]]
            normalized_clut = rgb555_without_stp(clut_payload)
            clut_key = (clut["width_words"], clut["height"], hashlib.sha256(normalized_clut).hexdigest())
            clut_item = {
                "catalog_index": catalog_index,
                "part": "clut",
                "block_index": record.get("block_index"),
                "container_offset": record["container_offset"],
                "width_words": clut["width_words"],
                "height": clut["height"],
                "payload_sha256": clut_key[2],
                "destination_from_tim": {"x_words": clut["x"], "y": clut["y"]},
            }
            tim_records.append(clut_item)
            tim_map.setdefault(clut_key, []).append(clut_item)

    uploads = []
    matches = []
    upload_dir = project / "work/runtime/vram_uploads"
    paths = sorted(upload_dir.glob("cpu_to_vram_copy_*.png"), key=lambda path: int(path.stem.rsplit("_", 1)[1]))
    for path in paths:
        with Image.open(path) as image:
            raw_words = rgb555_bytes_from_image(image)
            key = (image.width, image.height, hashlib.sha256(raw_words).hexdigest())
            upload = {
                "upload_id": int(path.stem.rsplit("_", 1)[1]),
                "file": path.relative_to(project).as_posix(),
                "width_words": image.width,
                "height": image.height,
                "rgb555_sha256": key[2],
            }
        uploads.append(upload)
        for tim_record in tim_map.get(key, []):
            matches.append({**upload, "tim": tim_record})

    target_match = [match for match in matches if match["tim"]["catalog_index"] == 5]
    target_image_match = [match for match in target_match if match["tim"]["part"] == "image"]
    report = {
        "schema_version": 1,
        "status": "PRIMARY_FONT_IMAGE_AND_CLUT_MATCHED" if target_image_match and any(match["tim"]["part"] == "clut" for match in target_match) else "PRIMARY_FONT_INCOMPLETE_MATCH",
        "comparison": "width_words, height, and all RGB555 payload words with STP/bit15 removed like DuckStation PNG dumps",
        "tim_count": len(inventory["records"]),
        "tim_part_count": len(tim_records),
        "upload_count": len(uploads),
        "matched_upload_count": len({item["upload_id"] for item in matches}),
        "matched_tim_count": len({item["tim"]["catalog_index"] for item in matches}),
        "block_match_counts": dict(sorted(Counter(str(item["tim"]["block_index"]) for item in matches).items())),
        "primary_font_matches": target_match,
        "matches": matches,
    }
    output = project / "work/analysis/vram_tim_matches.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
