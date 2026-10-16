#!/usr/bin/env python3
"""Audit whether a TTF can render every glyph the translation corpus requires at 16x16."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


NOTDEF_PROBE = ""  # private use area: never mapped, so it renders .notdef


def render_cell(character: str, font: ImageFont.FreeTypeFont) -> tuple[Image.Image, tuple[int, int, int, int]]:
    bbox = font.getbbox(character)
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    image = Image.new("L", (16, 16), 0)
    if width > 0 and height > 0 and width <= 16 and height <= 16:
        draw = ImageDraw.Draw(image)
        draw.text(((16 - width) // 2 - bbox[0], (16 - height) // 2 - bbox[1]), character, font=font, fill=255)
    return image, bbox


def probe_glyph(character: str, font: ImageFont.FreeTypeFont, threshold: int) -> dict:
    image, bbox = render_cell(character, font)
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    record = {"bbox": list(bbox), "width": width, "height": height}
    if width <= 0 or height <= 0:
        record["status"] = "EMPTY_BBOX"
        return record
    if width > 16 or height > 16:
        record["status"] = "OVERSIZE"
        return record
    ink = sum(1 for value in image.get_flattened_data() if value >= threshold)
    record["ink_pixels"] = ink
    if ink == 0:
        record["status"] = "NO_INK"
        return record
    # A .notdef box has ink but is not the requested glyph.
    if image.tobytes() == render_cell(NOTDEF_PROBE, font)[0].tobytes():
        record["status"] = "NOTDEF_BOX"
        return record
    record["status"] = "OK"
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--font", type=Path, default=Path(r"g:/Downloads/Korean Traslate Project/Font/Room No.703.ttf"))
    parser.add_argument("--fallback-fonts", default=r"g:/Downloads/Korean Traslate Project/Font/Galmuri11-Condensed.ttf",
                        help="';'-separated fallback TTFs tried in order for glyphs the primary cannot render")
    parser.add_argument("--size", type=int, default=16)
    parser.add_argument("--min-size", type=int, default=14)
    parser.add_argument("--threshold", type=int, default=128)
    args = parser.parse_args()
    project = args.project.resolve()
    census_path = project / "work/analysis/glyph_census_001.json"
    census = json.loads(census_path.read_text(encoding="utf-8"))
    required = census["census"]["required_glyphs"]
    if census["census"]["unique_required_glyphs"] != len(required):
        raise ValueError("census glyph count mismatch")

    candidates: list[tuple[str, Path, int]] = []
    for size in range(args.size, args.min_size - 1, -1):
        candidates.append(("primary", args.font, size))
    for fallback in args.fallback_fonts.split(";"):
        for size in range(args.size, args.min_size - 1, -1):
            candidates.append(("fallback", Path(fallback), size))
    font_cache: dict[tuple[Path, int], ImageFont.FreeTypeFont] = {}

    results: dict[str, dict] = {}
    failures: list[dict] = []
    fallback_assignments: list[dict] = []
    for glyph in required:
        record = None
        for role, path, size in candidates:
            font = font_cache.setdefault((path, size), ImageFont.truetype(str(path), size))
            probe = probe_glyph(glyph, font, args.threshold)
            if probe["status"] == "OK":
                record = {**probe, "font_role": role, "font": path.name, "size_px": size}
                break
        if record is None:
            record = {**probe, "font_role": None, "font": None, "size_px": None}
        record["nfc"] = unicodedata.normalize("NFC", glyph) == glyph
        results[glyph] = record
        if record["status"] != "OK" or not record["nfc"]:
            failures.append({"glyph": glyph, "codepoint": f"U+{ord(glyph):04X}", **record})
        elif record["font_role"] == "fallback" or record["size_px"] != args.size:
            fallback_assignments.append({"glyph": glyph, "codepoint": f"U+{ord(glyph):04X}",
                                         "font": record["font"], "size_px": record["size_px"]})

    class_counts: dict[str, int] = {}
    for glyph, record in results.items():
        key = record["status"] if record["font_role"] != "fallback" else "OK_VIA_FALLBACK"
        class_counts[key] = class_counts.get(key, 0) + 1
    status = "FULL_COVERAGE_WITH_ASSIGNMENTS" if not failures and fallback_assignments else (
        "FULL_COVERAGE" if not failures else "COVERAGE_GAPS")
    report = {
        "schema_version": 1,
        "observation_id": "FONT-COVERAGE-001",
        "status": status,
        "font": {
            "path": str(args.font),
            "sha256": hashlib.sha256(args.font.read_bytes()).hexdigest(),
            "size_px": args.size,
            "threshold": args.threshold,
        },
        "census": {
            "path": census_path.relative_to(project).as_posix(),
            "required_glyphs": len(required),
        },
        "fallbacks": args.fallback_fonts.split(";"),
        "status_counts": class_counts,
        "failure_count": len(failures),
        "failures": failures,
        "non_default_assignments": fallback_assignments,
        "size_stats": {
            "max_width": max(record["width"] for record in results.values()),
            "max_height": max(record["height"] for record in results.values()),
        },
        "glyph_plan": {glyph: {"font": record["font"], "size_px": record["size_px"]}
                       for glyph, record in results.items()},
        "interpretation": (
            "Coverage means every corpus glyph renders ink within a 16x16 cell using the primary font at the "
            "default size, a reduced size, or a fallback font. Aesthetic consistency of the non-default "
            "assignments stays a human review item."
        ),
    }
    output = project / "work/analysis/font_coverage_001.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "required": len(required), "failures": len(failures),
                      "status_counts": class_counts, "size_stats": report["size_stats"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
