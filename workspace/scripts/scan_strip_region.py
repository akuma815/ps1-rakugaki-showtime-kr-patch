#!/usr/bin/env python3
"""Find 16-row label strips anywhere in a byte range, without walking (STRIP-WALK-001).

Walking strip-to-strip works only while every entry is text: the staff-roll bank
also holds curve artwork whose stride is arbitrary, and one wrong stride shifts
everything after it. This scans (offset, stride) pairs directly instead, so
non-text entries are skipped rather than followed.

Cheap structural tests run first -- blank top and bottom rows, blank left and
right columns, a text-like ink ratio -- because the shear score is far too
expensive to run on every candidate.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from walk_strip_bank import ROWS, nibbles, render, stride_score

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def quick_reject(blob: bytes, offset: int, stride: int) -> bool:
    """True when this cannot be a text strip, using only cheap tests.

    Margins are NOT a usable signal here: measured on the five confirmed strips,
    these glyphs run edge to edge, filling the top row, the bottom row and the
    left column. What does hold is that the art is two-valued -- every nibble is
    0 or 15 -- and that ink covers roughly half the box.
    """
    span = stride * ROWS
    if offset + span > len(blob):
        return True
    for byte in blob[offset:offset + span]:
        if (byte & 0xF) not in (0, 0xF) or ((byte >> 4) & 0xF) not in (0, 0xF):
            return True
    ink = 0
    for byte in blob[offset:offset + span]:
        ink += (byte & 0xF) != 0
        ink += ((byte >> 4) & 0xF) != 0
    return not (0.30 <= ink / (span * 2) <= 0.72)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--container", default="extracted/HAGECHO.BIN")
    parser.add_argument("--from", dest="start", default="0x21E400")
    parser.add_argument("--to", dest="end", default="0x21FA00")
    parser.add_argument("--min-stride", type=int, default=20)
    parser.add_argument("--max-stride", type=int, default=96)
    parser.add_argument("--min-score", type=float, default=0.75)
    parser.add_argument("--scale", type=int, default=4)
    parser.add_argument("--out", default="work/graphics/traced_uploads/strip_scan.png")
    parser.add_argument("--report", default="work/analysis/strip_scan.json")
    args = parser.parse_args()
    project = args.project.resolve()
    blob = (project / args.container).read_bytes()

    hits = []
    for offset in range(int(args.start, 16), int(args.end, 16), 4):
        for stride in range(args.min_stride, args.max_stride + 1, 2):
            if quick_reject(blob, offset, stride):
                continue
            score = stride_score(blob, offset, stride)
            if score >= args.min_score:
                hits.append({"disc_offset": f"0x{offset:06X}", "stride_bytes": stride,
                             "width_px": stride * 2, "score": round(score, 3)})

    # Keep the best hit per overlapping cluster: a real strip also scores at
    # neighbouring offsets, and listing all of them buries the actual entries.
    hits.sort(key=lambda h: -h["score"])
    kept = []
    for hit in hits:
        offset = int(hit["disc_offset"], 16)
        span = hit["stride_bytes"] * ROWS
        if any(offset < int(k["disc_offset"], 16) + k["stride_bytes"] * ROWS
               and int(k["disc_offset"], 16) < offset + span for k in kept):
            continue
        kept.append(hit)
    kept.sort(key=lambda h: int(h["disc_offset"], 16))

    if kept:
        sheet_w = max(k["width_px"] for k in kept) * args.scale + 8
        sheet = Image.new("RGB", (sheet_w, len(kept) * (ROWS * args.scale + 15) + 6), (18, 18, 26))
        draw = ImageDraw.Draw(sheet)
        y = 4
        for entry in kept:
            draw.text((4, y), f"{entry['disc_offset']} {entry['width_px']}px score={entry['score']}",
                      fill=(250, 225, 80))
            y += 13
            sheet.paste(render(blob, int(entry["disc_offset"], 16), entry["stride_bytes"], args.scale), (4, y))
            y += ROWS * args.scale + 2
        (project / args.out).parent.mkdir(parents=True, exist_ok=True)
        sheet.save(project / args.out)

    (project / args.report).write_text(json.dumps(
        {"schema_version": 1, "observation_id": "STRIP-SCAN-001", "container": args.container,
         "range": [args.start, args.end], "candidates": len(hits), "strips": kept},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"candidates": len(hits), "kept": len(kept), "sheet": args.out}, indent=2))


if __name__ == "__main__":
    main()
