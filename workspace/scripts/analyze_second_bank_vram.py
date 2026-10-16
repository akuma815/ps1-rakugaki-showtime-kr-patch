#!/usr/bin/env python3
"""Shortlist second font-bank VRAM pages without claiming runtime safety."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from psx_tim import parse_tim


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def overlaps(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    project = args.project.resolve()
    inventory_path = project / "work/analysis/tim_inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    hagecho = (project / "extracted/HAGECHO.BIN").read_bytes()
    records = []
    for item in inventory["records"]:
        divisor = {4: 4, 8: 2, 16: 1}[item["bpp"]]
        rect = (
            item["image_vram"]["x"],
            item["image_vram"]["y"],
            item["width"] // divisor,
            item["height"],
        )
        records.append((item, rect))

    candidates = []
    for y in (0, 256):
        for x in range(320, 961, 64):
            rect = (x, y, 64, 256)
            image_hits = [
                {"block": item.get("block_index"), "index": item["index_in_source"], "rect_words": list(other)}
                for item, other in records
                if overlaps(rect, other)
            ]
            clut_hits = []
            for item in inventory["records"]:
                parsed = parse_tim(hagecho, item["container_offset"])
                if parsed is None:
                    raise ValueError(f"TIM inventory entry no longer parses: {item['container_offset']}")
                clut = parsed["clut"]
                clut_rect = (clut["x"], clut["y"], clut["width_words"], clut["height"])
                if overlaps(rect, clut_rect):
                    clut_hits.append({"block": item.get("block_index"), "index": item["index_in_source"], "rect_words": list(clut_rect)})
            candidates.append({
                "rect_words": list(rect),
                "tpage": (x // 64) | (0x10 if y == 256 else 0),
                "tim_image_overlap_count": len(image_hits),
                "tim_clut_overlap_count": len(clut_hits),
                "tim_image_overlaps": image_hits,
                "tim_clut_overlaps": clut_hits,
            })
    clear = [item for item in candidates if item["tim_image_overlap_count"] == 0 and item["tim_clut_overlap_count"] == 0]
    preferred = next(item for item in clear if item["rect_words"][:2] == [704, 256])
    report = {
        "schema_version": 1,
        "observation_id": "FONT-TWO-BANK-VRAM-001",
        "status": "STATIC_TIM_CLEAR_RUNTIME_UPLOAD_AUDIT_REQUIRED",
        "inventory": {
            "path": inventory_path.relative_to(project).as_posix(),
            "sha256": hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
            "tim_records": len(inventory["records"]),
        },
        "page_shape_words": [64, 256],
        "static_clear_candidate_count": len(clear),
        "preferred_probe": preferred,
        "all_candidates": candidates,
        "limits": [
            "TIM headers do not cover framebuffer rectangles, GPU clears, raw CPU-to-VRAM commands, or scene-specific non-TIM uploads.",
            "A zero static overlap count is a probe candidate, not proof of runtime safety or persistence.",
        ],
        "next_gate": "capture representative title, options, gameplay, transition, and result-state VRAM writes/occupancy for the preferred page before allocating final bank storage",
    }
    output = project / "work/analysis/font_two_bank_vram_001.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "clear": len(clear), "preferred": preferred}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
