#!/usr/bin/env python3
"""Catalog the raw menu-label bitmaps (MENU-GRAPHICS-001).

The Options screen uploads each label line as a raw 4bpp bitmap to the dynamic
page (704,256). For every 16-row band of the captured page this script finds
the source bytes in HAGECHO.BIN, measures the row stride that keeps all 16 rows
matching, and emits a catalog plus a review PNG per strip.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from PIL import Image


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

VRAM_ROW_BYTES = 128
BAND_ROWS = 16


def render_strip(data: bytes, offset: int, stride: int, rows: int, target: Path) -> None:
    image = Image.new("L", (stride * 2, rows), 0)
    pixels = image.load()
    for y in range(rows):
        for i in range(stride):
            value = data[offset + y * stride + i]
            pixels[i * 2, y] = (value & 0xF) * 17
            pixels[i * 2 + 1, y] = ((value >> 4) & 0xF) * 17
    image.resize((stride * 2 * 3, rows * 3), Image.Resampling.NEAREST).save(target)


def measure_band(hagecho: bytes, rect: bytes, band: int) -> dict | None:
    base_row = band * BAND_ROWS
    rows = [rect[(base_row + k) * VRAM_ROW_BYTES:(base_row + k) * VRAM_ROW_BYTES + VRAM_ROW_BYTES]
            for k in range(BAND_ROWS)]
    ink_rows = [row for row in rows if any(row)]
    if not ink_rows:
        return None
    # anchor on the densest row to survive mostly-empty leading rows
    anchor_index = max(range(BAND_ROWS), key=lambda k: sum(1 for b in rows[k] if b))
    anchor = rows[anchor_index]
    probe = anchor[:24]
    candidates = []
    start = 0
    while len(candidates) < 32:
        j = hagecho.find(probe, start)
        if j < 0:
            break
        candidates.append(j)
        start = j + 1
    for j in candidates:
        # A VRAM row may hold two side-by-side strips stored at different disc
        # locations, so every plausible stride is tested, narrow to wide.
        for stride in range(24, 129, 4):
            origin = j - anchor_index * stride
            if origin < 0:
                continue
            ok = all(
                hagecho[origin + k * stride: origin + k * stride + min(stride, VRAM_ROW_BYTES)]
                == rows[k][:min(stride, VRAM_ROW_BYTES)]
                for k in range(BAND_ROWS)
            )
            if ok:
                return {"band": band, "vram_rows": [base_row, base_row + BAND_ROWS - 1],
                        "disc_offset": origin, "stride_bytes": stride, "width_px": stride * 2,
                        "height_rows": BAND_ROWS,
                        "sha256": hashlib.sha256(
                            hagecho[origin: origin + BAND_ROWS * stride]).hexdigest()}
    return {"band": band, "vram_rows": [base_row, base_row + BAND_ROWS - 1],
            "disc_offset": None, "note": "no consistent disc mapping found",
            "probe_hex": probe.hex()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--rect", type=Path,
                        default=Path("work/runtime/vram_occupancy_003/probe_001_rect.bin"))
    parser.add_argument("--label", default="options")
    args = parser.parse_args()
    project = args.project.resolve()
    hagecho = (project / "extracted/HAGECHO.BIN").read_bytes()
    rect_path = args.rect if args.rect.is_absolute() else project / args.rect
    rect = rect_path.read_bytes()

    graphics_dir = project / "work/graphics" / f"menu_strips_{args.label}"
    graphics_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for band in range(len(rect) // (VRAM_ROW_BYTES * BAND_ROWS)):
        entry = measure_band(hagecho, rect, band)
        if entry is None:
            continue
        if entry.get("disc_offset") is not None:
            png = graphics_dir / f"band_{band:02d}_0x{entry['disc_offset']:X}.png"
            render_strip(hagecho, entry["disc_offset"], entry["stride_bytes"],
                         entry["height_rows"], png)
            entry["preview"] = png.relative_to(project).as_posix()
        entries.append(entry)

    mapped = [e for e in entries if e.get("disc_offset") is not None]
    report = {
        "schema_version": 1,
        "observation_id": "MENU-GRAPHICS-001",
        "role": f"strip catalog ({args.label})",
        "status": "CATALOGED" if mapped and len(mapped) == len(entries) else "PARTIAL",
        "source_rect": rect_path.relative_to(project).as_posix() if rect_path.is_relative_to(project) else str(rect_path),
        "bands_with_ink": len(entries),
        "bands_mapped": len(mapped),
        "entries": entries,
    }
    out = project / "work/analysis" / f"menu_strips_{args.label}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "bands_with_ink": len(entries),
                      "bands_mapped": len(mapped)}, indent=2))
    for e in entries:
        if e.get("disc_offset") is not None:
            print(f"  band {e['band']:2d}: 0x{e['disc_offset']:X} stride {e['stride_bytes']} ({e['width_px']}px)")
        else:
            print(f"  band {e['band']:2d}: UNMAPPED")


if __name__ == "__main__":
    main()
