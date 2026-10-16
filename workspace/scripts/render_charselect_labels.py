#!/usr/bin/env python3
"""Render original and proposed 8-row character-select labels for visual QA."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

from render_menu_strips import encode_strip_4bpp
from render_traced_uploads import decode_4bpp


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--out", default="work/graphics/charselect_labels_preview.png")
    args = parser.parse_args()
    project = args.project.resolve()
    config = json.loads((project / "workspace/config/charselect_labels.json").read_text(encoding="utf-8"))
    hagecho = (project / "extracted/HAGECHO.BIN").read_bytes()
    scale = 8
    max_width = max(label["width_px"] for label in config["labels"])
    row_h = 20 + 8 * scale
    sheet = Image.new("RGB", (max_width * scale * 2 + 32, row_h * len(config["labels"])), (18, 18, 24))
    draw = ImageDraw.Draw(sheet)
    for index, label in enumerate(config["labels"]):
        y = index * row_h
        draw.text((4, y), f"{label['japanese']} -> {label['korean']}  {label['disc_offset']}", fill=(255, 230, 80))
        original = decode_4bpp(hagecho, int(label["disc_offset"], 16), label["width_px"], label["height_rows"])
        encoded = encode_strip_4bpp(label["korean"], label["stride_bytes"], height=label["height_rows"])
        korean = decode_4bpp(encoded, 0, label["width_px"], label["height_rows"])
        render_size = (label["width_px"] * scale, label["height_rows"] * scale)
        sheet.paste(original.convert("RGB").resize(render_size, Image.Resampling.NEAREST), (4, y + 18))
        sheet.paste(korean.convert("RGB").resize(render_size, Image.Resampling.NEAREST),
                    (max_width * scale + 24, y + 18))
    target = project / args.out
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target)
    print(json.dumps({"labels": len(config["labels"]), "output": target.relative_to(project).as_posix()}, indent=2))


if __name__ == "__main__":
    main()
