#!/usr/bin/env python3
"""Render every located LoadImage upload as an image so text can be read off it.

The trace tells us each upload's disc offset and its destination RECT, but a
RECT is not a name. Only 52 of 768 located uploads were ever identified, and the
words still missing from the translation (スタート!, クリア!, チーム ...) are
most likely sitting in that unidentified remainder -- never rendered, so never
recognised.

A LoadImage RECT is measured in 16-bit VRAM words, and these strips are 4bpp, so
the pixel width is rect.w * 4. Contact sheets are emitted at 3x nearest-neighbour
so Japanese katakana stays legible.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SCALE = 3
PAD = 6
LABEL_H = 12


def decode_4bpp(blob: bytes, offset: int, width_px: int, height: int) -> Image.Image:
    """Unpack a raw 4bpp strip: low nibble is the even pixel, high nibble odd."""
    stride = width_px // 2
    image = Image.new("L", (width_px, height), 0)
    pixels = image.load()
    for y in range(height):
        row = offset + y * stride
        if row + stride > len(blob):
            break
        for i in range(stride):
            byte = blob[row + i]
            pixels[i * 2, y] = (byte & 0xF) * 17
            pixels[i * 2 + 1, y] = ((byte >> 4) & 0xF) * 17
    return image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--located", default="work/analysis/traced003_located.json")
    parser.add_argument("--out", default="work/graphics/traced_uploads")
    parser.add_argument("--min-width", type=int, default=32)
    parser.add_argument("--min-height", type=int, default=10)
    parser.add_argument("--per-sheet", type=int, default=40)
    parser.add_argument("--scale", type=int, default=SCALE)
    parser.add_argument("--sort", choices=("width", "elapsed", "offset"), default="width")
    parser.add_argument("--skip-known", action="store_true",
                        help="omit uploads already identified in menu_strips_catalog.json")
    args = parser.parse_args()
    project = args.project.resolve()

    located = json.loads((project / args.located).read_text(encoding="utf-8"))
    known: set[int] = set()
    if args.skip_known:
        catalog = json.loads((project / "work/analysis/menu_strips_catalog.json").read_text(encoding="utf-8"))
        known = {int(e["disc_offset"], 16) for e in catalog["entries"]}

    blobs = {
        "HAGECHO": (project / "extracted/HAGECHO.BIN").read_bytes(),
        "ARA": (project / "extracted/ARA.BIN").read_bytes(),
    }

    picks = []
    for entry in located:
        offset = int(entry["offset"], 16)
        if offset in known:
            continue
        rect = entry["rect"]
        width_px, height = rect[2] * 4, rect[3]
        if width_px < args.min_width or height < args.min_height:
            continue
        blob = blobs.get(entry["file"].replace("_patched", ""))
        if blob is None or offset + (width_px // 2) * height > len(blob):
            continue
        picks.append((entry, width_px, height, decode_4bpp(blob, offset, width_px, height)))

    if args.sort == "elapsed":
        picks.sort(key=lambda p: p[0].get("elapsed_s", 0))
    elif args.sort == "offset":
        picks.sort(key=lambda p: int(p[0]["offset"], 16))
    else:
        picks.sort(key=lambda p: -p[1])
    out_dir = project / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("sheet_*.png"):
        old.unlink()

    sheets = []
    for start in range(0, len(picks), args.per_sheet):
        chunk = picks[start:start + args.per_sheet]
        width = max(p[1] for p in chunk) * args.scale + 16
        height = sum(p[2] * args.scale + PAD + LABEL_H for p in chunk) + 8
        sheet = Image.new("RGB", (width, height), (20, 20, 28))
        draw = ImageDraw.Draw(sheet)
        y = 4
        for entry, width_px, strip_h, image in chunk:
            draw.text((4, y), f"{entry['file']} {entry['offset']} {width_px}x{strip_h}", fill=(250, 230, 90))
            y += LABEL_H
            sheet.paste(image.convert("RGB").resize((width_px * args.scale, strip_h * args.scale), Image.NEAREST), (8, y))
            y += strip_h * args.scale + PAD
        path = out_dir / f"sheet_{start // args.per_sheet:02d}.png"
        sheet.save(path)
        sheets.append(path.relative_to(project).as_posix())

    print(json.dumps({"rendered": len(picks), "sheets": sheets}, indent=2))


if __name__ == "__main__":
    main()
