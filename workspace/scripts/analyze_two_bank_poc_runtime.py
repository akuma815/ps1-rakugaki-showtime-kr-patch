#!/usr/bin/env python3
"""Create a review contact sheet and record the two-bank PoC capture scope."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--session-id", default="font_two_bank_poc_001_runtime_motion")
    args = parser.parse_args()
    project = args.project.resolve()
    session_path = project / "work/analysis" / f"{args.session_id}.json"
    session = json.loads(session_path.read_text(encoding="utf-8"))
    if session["status"] != "CAPTURED_PENDING_REVIEW" or not session["captures"]:
        raise ValueError("runtime capture is incomplete")

    frames: list[tuple[float, Image.Image]] = []
    for record in session["captures"]:
        source = Image.open(project / record["screenshot"]).convert("RGB")
        crop_width = min(960, source.width)
        crop_height = min(704, source.height)
        viewport = source.crop((source.width - crop_width, source.height - crop_height, source.width, source.height))
        viewport.thumbnail((320, 235), Image.Resampling.LANCZOS)
        frames.append((record["elapsed_seconds"], viewport.copy()))

    columns = 4
    cell_width, cell_height = 320, 260
    rows = (len(frames) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), "#202020")
    draw = ImageDraw.Draw(sheet)
    for index, (elapsed, frame) in enumerate(frames):
        x = (index % columns) * cell_width
        y = (index // columns) * cell_height
        sheet.paste(frame, (x, y + 20))
        draw.text((x + 4, y + 3), f"t={elapsed:.1f}s", fill="white")
    output_dir = project / "work/graphics/font_two_bank_poc_001"
    output_dir.mkdir(parents=True, exist_ok=True)
    sheet_path = output_dir / "runtime_motion_contact_sheet.png"
    sheet.save(sheet_path)

    report = {
        "schema_version": 1,
        "observation_id": "FONT-TWO-BANK-POC-001",
        "status": "CAPTURE_REVIEW_REQUIRED",
        "source_session": session_path.relative_to(project).as_posix(),
        "source_session_sha256": sha256(session_path),
        "capture_count": len(frames),
        "capture_method": "DuckStation window capture; lower game viewport is clipped by desktop bounds",
        "contact_sheet": {
            "path": sheet_path.relative_to(project).as_posix(),
            "sha256": sha256(sheet_path),
        },
        "static_build_claim": "bank-aware decoder return and builder tpage patch are statically verified",
        "runtime_claim": "pending visual confirmation because the current window captures do not keep the full game viewport in frame",
        "next_gate": "capture the full title viewport or confirm the moving 한글 prefix directly in DuckStation",
    }
    report_path = project / "work/analysis/font_two_bank_poc_001_runtime_review.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
