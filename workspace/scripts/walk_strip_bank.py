#!/usr/bin/env python3
"""Walk a run of consecutive 16-row 4bpp label strips, recovering each stride.

The staff-roll title cards sit back to back in HAGECHO with no header and a
different width per word, so a fixed-stride sweep desynchronises after the first
entry and everything downstream renders as diagonal noise.

Stride is recovered from the data instead: at the correct stride each row lines
up with the row above, while at a wrong stride the image shears, so every row
matches the previous one only after a constant horizontal shift. Scoring the
best shift per row and preferring the stride whose rows agree at shift zero
picks the width out without any guessing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROWS = 16
MAX_SHIFT = 6


def nibbles(blob: bytes, offset: int, stride: int, row: int) -> list[int]:
    out = []
    for i in range(stride):
        byte = blob[offset + row * stride + i]
        out.append(byte & 0xF)
        out.append((byte >> 4) & 0xF)
    return out


def stride_score(blob: bytes, offset: int, stride: int) -> float:
    """How strongly this stride makes rows line up with no shear."""
    if offset + stride * ROWS > len(blob):
        return -1.0
    rows = [nibbles(blob, offset, stride, r) for r in range(ROWS)]
    if not any(any(r) for r in rows):
        return -1.0
    zero_wins = 0
    compared = 0
    for r in range(1, ROWS):
        prev, cur = rows[r - 1], rows[r]
        best_shift, best_hits = 0, -1
        for shift in range(-MAX_SHIFT, MAX_SHIFT + 1):
            hits = 0
            for x in range(len(cur)):
                j = x + shift
                if 0 <= j < len(prev) and cur[x] == prev[j]:
                    hits += 1
            if hits > best_hits:
                best_hits, best_shift = hits, shift
        compared += 1
        if best_shift == 0:
            zero_wins += 1
    # padding rows at top and bottom are the norm for these 16-row strips
    pad = (0.5 if not any(rows[0]) else 0.0) + (0.5 if not any(rows[-1]) else 0.0)
    # A strip sliced at half its true stride still lines up row-to-row, so the
    # shear test alone cannot tell 40px from 80px. A real strip has a left and
    # right margin; a half-width slice cuts through glyphs instead.
    left = 0.5 if not any(r[0] for r in rows) else 0.0
    right = 0.5 if not any(r[-1] for r in rows) else 0.0
    # The decisive half-stride test: read at half the true width, the rows
    # alternate between the left and right halves of each real row, so rows two
    # apart (same half) agree far better than neighbours. At the true stride the
    # two agree about equally.
    def agree(a: list[int], b: list[int]) -> float:
        return sum(1 for x, y in zip(a, b) if x == y) / max(1, len(a))
    near = sum(agree(rows[r - 1], rows[r]) for r in range(1, ROWS)) / (ROWS - 1)
    skip = sum(agree(rows[r - 2], rows[r]) for r in range(2, ROWS)) / (ROWS - 2)
    interleaved = max(0.0, skip - near - 0.02)
    return zero_wins / compared + pad * 0.25 + (left + right) * 0.25 - interleaved * 4.0


def render(blob: bytes, offset: int, stride: int, scale: int) -> Image.Image:
    width = stride * 2
    image = Image.new("RGB", (width, ROWS), (35, 35, 50))
    pixels = image.load()
    for y in range(ROWS):
        row = nibbles(blob, offset, stride, y)
        for x, value in enumerate(row):
            pixels[x, y] = (255, 255, 255) if value else (35, 35, 50)
    return image.resize((width * scale, ROWS * scale), Image.NEAREST)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--container", default="extracted/HAGECHO.BIN")
    parser.add_argument("--start", default="0x21D92C")
    parser.add_argument("--count", type=int, default=24)
    parser.add_argument("--min-stride", type=int, default=12)
    parser.add_argument("--max-stride", type=int, default=120)
    parser.add_argument("--scale", type=int, default=4)
    parser.add_argument("--out", default="work/graphics/traced_uploads/strip_walk.png")
    parser.add_argument("--report", default="work/analysis/strip_walk.json")
    args = parser.parse_args()
    project = args.project.resolve()
    blob = (project / args.container).read_bytes()

    offset = int(args.start, 16)
    found = []
    for _ in range(args.count):
        # tie-break toward the wider stride: a half-width slice scores almost as
        # well as the true width, and picking it desynchronises the whole walk
        scored = [(stride_score(blob, offset, s), s) for s in range(args.min_stride, args.max_stride + 1, 2)]
        best_score = max((sc for sc, _ in scored), default=-1)
        if best_score < 0:
            break
        stride = max(s for sc, s in scored if sc >= best_score - 1e-9)
        score = best_score
        if stride == 0:
            break
        found.append({"disc_offset": f"0x{offset:06X}", "stride_bytes": stride,
                      "width_px": stride * 2, "score": round(score, 3)})
        offset += stride * ROWS

    sheet_w = max(f["width_px"] for f in found) * args.scale + 8
    sheet_h = len(found) * (ROWS * args.scale + 15) + 6
    sheet = Image.new("RGB", (sheet_w, sheet_h), (18, 18, 26))
    draw = ImageDraw.Draw(sheet)
    y = 4
    for entry in found:
        draw.text((4, y), f"{entry['disc_offset']} {entry['width_px']}px score={entry['score']}",
                  fill=(250, 225, 80))
        y += 13
        sheet.paste(render(blob, int(entry["disc_offset"], 16), entry["stride_bytes"], args.scale), (4, y))
        y += ROWS * args.scale + 2
    (project / args.out).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(project / args.out)
    (project / args.report).write_text(json.dumps(
        {"schema_version": 1, "observation_id": "STRIP-WALK-001",
         "container": args.container, "start": args.start, "strips": found},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"strips": len(found), "sheet": args.out}, indent=2))


if __name__ == "__main__":
    main()
