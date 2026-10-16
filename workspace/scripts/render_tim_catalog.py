#!/usr/bin/env python3
"""Render the surveyed TIM assets and numbered contact sheets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

from psx_tim import decode_tim, parse_tim


CELL_W = 300
CELL_H = 230
THUMB_W = 280
THUMB_H = 190
COLS = 4
PER_SHEET = 40


def composite_thumbnail(source: Image.Image) -> Image.Image:
    thumb = source.copy()
    thumb.thumbnail((THUMB_W, THUMB_H), Image.Resampling.NEAREST)
    backdrop = Image.new("RGBA", thumb.size, (64, 64, 64, 255))
    backdrop.alpha_composite(thumb)
    return backdrop.convert("RGB")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    project = args.project.resolve()
    inventory_path = project / "work/analysis/tim_inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    hagecho = (project / "extracted/HAGECHO.BIN").read_bytes()
    ara = (project / "extracted/ARA.BIN").read_bytes()
    output_dir = project / "work/graphics/tim"
    output_dir.mkdir(parents=True, exist_ok=True)

    rendered = []
    for catalog_index, record in enumerate(inventory["records"]):
        data = ara if record["source"] == "ARA.BIN" else hagecho
        offset = record["container_offset"]
        tim = parse_tim(data, offset)
        if tim is None:
            raise ValueError(f"catalog record no longer parses: {catalog_index}")
        image = decode_tim(data, tim)
        block = record.get("block_index")
        name = f"tim_{catalog_index:03d}_block_{block:03d}_{offset:08x}_{image.width}x{image.height}x{record['bpp']}.png"
        destination = output_dir / name
        image.save(destination)
        rendered.append({
            "catalog_index": catalog_index,
            "block_index": block,
            "container_offset": offset,
            "png": destination.relative_to(project).as_posix(),
            "width": image.width,
            "height": image.height,
            "bpp": record["bpp"],
        })

    sheet_dir = project / "work/graphics/tim_contact_sheets"
    sheet_dir.mkdir(parents=True, exist_ok=True)
    sheet_paths = []
    for sheet_index, first in enumerate(range(0, len(rendered), PER_SHEET)):
        page = rendered[first : first + PER_SHEET]
        rows = (len(page) + COLS - 1) // COLS
        sheet = Image.new("RGB", (COLS * CELL_W, rows * CELL_H), (30, 30, 30))
        draw = ImageDraw.Draw(sheet)
        for position, item in enumerate(page):
            col, row = position % COLS, position // COLS
            image = Image.open(project / item["png"]).convert("RGBA")
            thumb = composite_thumbnail(image)
            x = col * CELL_W + (CELL_W - thumb.width) // 2
            y = row * CELL_H + 28 + (THUMB_H - thumb.height) // 2
            sheet.paste(thumb, (x, y))
            label = f"#{item['catalog_index']:03d} B{item['block_index']:03d} {item['width']}x{item['height']} {item['bpp']}bpp"
            draw.text((col * CELL_W + 8, row * CELL_H + 7), label, fill=(255, 255, 255))
        sheet_path = sheet_dir / f"contact_sheet_{sheet_index:02d}.png"
        sheet.save(sheet_path)
        sheet_paths.append(sheet_path.relative_to(project).as_posix())

    report = {"schema_version": 1, "rendered_count": len(rendered), "contact_sheets": sheet_paths, "records": rendered}
    report_path = project / "work/analysis/tim_render_catalog.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"rendered_count={len(rendered)}")
    print(f"contact_sheets={len(sheet_paths)}")
    print(report_path)


if __name__ == "__main__":
    main()
