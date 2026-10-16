#!/usr/bin/env python3
"""Render the primary 16x16-cell font candidate with code labels and all CLUTs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

from psx_tim import decode_tim, parse_tim


GRID = 16
CELL = 16
SCALE = 3
LABEL_H = 15


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    project = args.project.resolve()
    targets = json.loads((project / "workspace/config/asset_targets.json").read_text(encoding="utf-8"))
    target = targets["targets"]["primary_font_atlas"]
    hagecho = (project / "extracted/HAGECHO.BIN").read_bytes()
    offset = target["container_offset"]
    tim = parse_tim(hagecho, offset)
    if tim is None:
        raise ValueError("primary font target is not a valid TIM")
    if (tim["pixel_width"], tim["pixel_height"], tim["bpp"]) != (256, 256, 4):
        raise ValueError("primary font target dimensions changed")
    palette_count = tim["clut"]["width_words"] * tim["clut"]["height"] // 16
    output_dir = project / "work/graphics/font_candidate_005"
    output_dir.mkdir(parents=True, exist_ok=True)

    palette_outputs = []
    for palette_index in range(palette_count):
        image = decode_tim(hagecho, tim, palette_index)
        plain_path = output_dir / f"font_palette_{palette_index:02d}.png"
        image.save(plain_path)

        cell_w = CELL * SCALE
        cell_h = LABEL_H + CELL * SCALE
        sheet = Image.new("RGB", (GRID * cell_w, GRID * cell_h), (24, 24, 24))
        draw = ImageDraw.Draw(sheet)
        occupied = []
        for code in range(256):
            col, row = code % GRID, code // GRID
            crop = image.crop((col * CELL, row * CELL, (col + 1) * CELL, (row + 1) * CELL))
            alpha = crop.getchannel("A")
            bbox = alpha.getbbox()
            occupied.append(bbox is not None)
            enlarged = crop.resize((CELL * SCALE, CELL * SCALE), Image.Resampling.NEAREST)
            backdrop = Image.new("RGBA", enlarged.size, (64, 64, 64, 255))
            backdrop.alpha_composite(enlarged)
            x, y = col * cell_w, row * cell_h
            sheet.paste(backdrop.convert("RGB"), (x, y + LABEL_H))
            draw.text((x + 2, y + 1), f"{code:02X}", fill=(255, 255, 255))
            draw.rectangle((x, y, x + cell_w - 1, y + cell_h - 1), outline=(96, 96, 96))
        labeled_path = output_dir / f"font_palette_{palette_index:02d}_labeled.png"
        sheet.save(labeled_path)
        palette_outputs.append({
            "palette_index": palette_index,
            "plain_png": plain_path.relative_to(project).as_posix(),
            "labeled_png": labeled_path.relative_to(project).as_posix(),
            "occupied_cell_count": sum(occupied),
            "empty_codes": [f"{code:02X}" for code, value in enumerate(occupied) if not value],
        })

    report = {
        "schema_version": 1,
        "status": "CANDIDATE_NOT_RUNTIME_CONFIRMED",
        "source": "HAGECHO.BIN",
        "block_index": target["block_index"],
        "container_offset": offset,
        "tim_total_size": tim["total_size"],
        "tim_bpp": tim["bpp"],
        "image_vram": {"x_words": tim["image"]["x"], "y": tim["image"]["y"]},
        "clut_vram": {"x": tim["clut"]["x"], "y": tim["clut"]["y"]},
        "palette_count": palette_count,
        "grid": {"columns": GRID, "rows": GRID, "cell_width": CELL, "cell_height": CELL, "hypothesis": "code byte equals cell index"},
        "palettes": palette_outputs,
    }
    report_path = project / "work/analysis/font_candidate_005.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"palette_count={palette_count}")
    print(f"grid={GRID}x{GRID} cell={CELL}x{CELL}")
    print(report_path)


if __name__ == "__main__":
    main()
