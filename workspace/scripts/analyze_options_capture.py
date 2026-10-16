#!/usr/bin/env python3
"""Relate the interactive Options capture to the primary font TIM evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

from PIL import Image

from psx_tim import parse_tim


FULL_FONT_RE = re.compile(
    r"^texupload-P4-(?P<texture>[0-9A-F]+)-(?P<clut>[0-9A-F]+)-64x256-0-0-256x256-P0-15\.png$"
)


def rgb555_sha256(image: Image.Image) -> str:
    output = bytearray()
    for red, green, blue in image.convert("RGB").get_flattened_data():
        value = round(red * 31 / 255) | (round(green * 31 / 255) << 5) | (round(blue * 31 / 255) << 10)
        output.extend(struct.pack("<H", value))
    return hashlib.sha256(output).hexdigest()


def payload_rgb555_sha256(payload: bytes) -> str:
    values = struct.unpack(f"<{len(payload) // 2}H", payload)
    normalized = struct.pack(f"<{len(values)}H", *(value & 0x7FFF for value in values))
    return hashlib.sha256(normalized).hexdigest()


def equal_pixels(left: Image.Image, right: Image.Image) -> tuple[int, int]:
    left_rgb = left.convert("RGB")
    right_rgb = right.convert("RGB")
    if left_rgb.size != right_rgb.size:
        return 0, left_rgb.width * left_rgb.height
    equal = sum(a == b for a, b in zip(left_rgb.get_flattened_data(), right_rgb.get_flattened_data()))
    return equal, left_rgb.width * left_rgb.height


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--session-id", default="options_session_001")
    args = parser.parse_args()

    project = args.project.resolve()
    session_root = project / "work/runtime" / args.session_id
    textures_root = session_root / "textures"
    uploads_root = session_root / "vram_uploads"
    screenshot_paths = sorted((session_root / "screenshots").glob("*.png"))
    palette_paths = sorted((project / "work/graphics/font_candidate_005").glob("font_palette_??.png"))
    palettes = [(int(path.stem[-2:]), Image.open(path).copy()) for path in palette_paths]

    targets = json.loads((project / "workspace/config/asset_targets.json").read_text(encoding="utf-8"))
    offset = targets["targets"]["primary_font_atlas"]["container_offset"]
    hagecho = (project / "extracted/HAGECHO.BIN").read_bytes()
    tim = parse_tim(hagecho, offset)
    if tim is None:
        raise ValueError("primary font TIM no longer parses")
    image_meta = tim["image"]
    image_payload = hagecho[
        image_meta["payload_offset"] : image_meta["payload_offset"] + image_meta["payload_size"]
    ]
    expected_upload_hash = payload_rgb555_sha256(image_payload)

    font_uploads = []
    for path in sorted(uploads_root.glob("cpu_to_vram_copy_*.png")):
        with Image.open(path) as image:
            if image.size == (image_meta["width_words"], image_meta["height"]):
                actual_hash = rgb555_sha256(image)
                if actual_hash == expected_upload_hash:
                    font_uploads.append(
                        {
                            "file": path.relative_to(project).as_posix(),
                            "rgb555_sha256": actual_hash,
                        }
                    )

    texture_matches = []
    for path in sorted(textures_root.glob("*.png")):
        match = FULL_FONT_RE.match(path.name)
        if not match:
            continue
        with Image.open(path) as image:
            comparisons = []
            for palette_index, palette in palettes:
                equal, total = equal_pixels(image, palette)
                comparisons.append(
                    {
                        "palette_index": palette_index,
                        "equal_pixels": equal,
                        "total_pixels": total,
                        "match_rate": equal / total,
                    }
                )
        comparisons.sort(key=lambda item: item["equal_pixels"], reverse=True)
        texture_matches.append(
            {
                "file": path.relative_to(project).as_posix(),
                "duckstation_texture_hash": match.group("texture"),
                "duckstation_clut_hash": match.group("clut"),
                "best_palette_matches": comparisons[:3],
            }
        )

    crop_paths = sorted(textures_root.glob("texupload-P4-*-64x256-16-176-228x80-P0-15.png"))
    exact_palette_matches = [
        item
        for item in texture_matches
        if item["best_palette_matches"] and item["best_palette_matches"][0]["match_rate"] == 1.0
    ]
    status = "PRIMARY_FONT_SAMPLED" if exact_palette_matches else "FONT_SIZED_TEXTURE_ONLY"
    report = {
        "schema_version": 1,
        "observation_id": "FONT-CONSUME-001",
        "status": status,
        "session_id": args.session_id,
        "screenshot_count": len(screenshot_paths),
        "screenshots": [path.relative_to(project).as_posix() for path in screenshot_paths],
        "font_tim": {
            "source": "HAGECHO.BIN",
            "container_offset": offset,
            "dimensions_pixels": [256, 256],
            "upload_dimensions_words": [image_meta["width_words"], image_meta["height"]],
            "expected_upload_rgb555_sha256": expected_upload_hash,
        },
        "matching_font_uploads": font_uploads,
        "full_texture_samples": texture_matches,
        "exact_palette_match_count": len(exact_palette_matches),
        "sampled_region_files": [path.relative_to(project).as_posix() for path in crop_paths],
        "sampled_region": {"x": 16, "y": 176, "width": 228, "height": 80},
        "cell_grid_intersection": {
            "columns": [1, 15],
            "rows": [11, 15],
            "note": "The right and bottom edge are partial; exact primitive UVs are not encoded in the aggregate dump name.",
        },
    }
    output = project / "work/analysis" / f"{args.session_id}_font_consume.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "font_uploads": len(font_uploads), "full_samples": len(texture_matches), "exact_palette_matches": len(exact_palette_matches)}, ensure_ascii=False))
    print(output)


if __name__ == "__main__":
    main()
