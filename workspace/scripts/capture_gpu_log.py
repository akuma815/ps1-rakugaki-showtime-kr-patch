#!/usr/bin/env python3
"""Capture DuckStation GPU debug logs and correlate upload coordinates to TIM matches."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path


UPLOAD_RE = re.compile(r"Copy rectangle from CPU to VRAM offset=\((\d+),(\d+)\), size=\((\d+),(\d+)\)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--duckstation", type=Path, default=Path(r"H:\Duckstation"))
    parser.add_argument("--seconds", type=float, default=20.0)
    args = parser.parse_args()
    project = args.project.resolve()
    duckstation = args.duckstation.resolve()
    exe = duckstation / "duckstation-qt-x64-ReleaseLTCG.exe"
    cue = duckstation / "Roms/Rakugaki Showtime (J)/Rakugaki Showtime (Japan).cue"
    settings_path = duckstation / "settings.ini"
    log_path = duckstation / "duckstation.log"
    settings_before = settings_path.read_bytes()
    settings_hash = hashlib.sha256(settings_before).hexdigest()
    log_before = log_path.read_bytes() if log_path.exists() else None
    text = settings_before.decode("utf-8")
    replacements = {
        "LogLevel = None": "LogLevel = Debug",
        "LogToFile = false": "LogToFile = true",
    }
    for old, new in replacements.items():
        if text.count(old) != 1:
            raise ValueError(f"expected one settings line: {old}")
        text = text.replace(old, new)

    process = None
    captured_log = b""
    try:
        settings_path.write_bytes(text.encode("utf-8"))
        command = [str(exe), "-batch", "-nogui", "-fastboot", "--", str(cue)]
        process = subprocess.Popen(command, cwd=duckstation, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        time.sleep(args.seconds)
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        if not log_path.exists():
            raise ValueError("DuckStation did not create duckstation.log")
        captured_log = log_path.read_bytes()
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        settings_path.write_bytes(settings_before)
        if log_before is None:
            if log_path.exists():
                log_path.unlink()
        else:
            log_path.write_bytes(log_before)
    if hashlib.sha256(settings_path.read_bytes()).hexdigest() != settings_hash:
        raise ValueError("DuckStation settings restoration failed")

    output_log = project / "work/runtime/duckstation_gpu_trace.log"
    output_log.parent.mkdir(parents=True, exist_ok=True)
    output_log.write_bytes(captured_log)
    log_text = captured_log.decode("utf-8", errors="replace")
    uploads = []
    for upload_id, match in enumerate(UPLOAD_RE.finditer(log_text), 1):
        x, y, width, height = (int(value) for value in match.groups())
        uploads.append({"upload_id": upload_id, "x_words": x, "y": y, "width_words": width, "height": height})

    tim_matches = json.loads((project / "work/analysis/vram_tim_matches.json").read_text(encoding="utf-8"))
    upload_map = {item["upload_id"]: item for item in uploads}
    correlated = []
    for match in tim_matches["matches"]:
        command = upload_map.get(match["upload_id"])
        if command is None:
            continue
        expected = match["tim"]["destination_from_tim"]
        coordinate_match = command["x_words"] == expected["x_words"] and command["y"] == expected["y"]
        dimension_match = command["width_words"] == match["width_words"] and command["height"] == match["height"]
        correlated.append({
            "upload_id": match["upload_id"],
            "catalog_index": match["tim"]["catalog_index"],
            "command": command,
            "tim_destination": expected,
            "coordinate_match": coordinate_match,
            "dimension_match": dimension_match,
        })
    primary = [item for item in correlated if item["catalog_index"] == 5]
    status = "PRIMARY_FONT_COORDINATES_MATCHED" if primary and all(item["coordinate_match"] and item["dimension_match"] for item in primary) else "PRIMARY_FONT_COORDINATES_NOT_MATCHED"
    report = {
        "schema_version": 1,
        "status": status,
        "duckstation_version": "0.1.11636",
        "capture_seconds": args.seconds,
        "settings_restored": True,
        "settings_sha256": settings_hash,
        "log": output_log.relative_to(project).as_posix(),
        "logged_upload_count": len(uploads),
        "prior_dump_upload_count": tim_matches["upload_count"],
        "primary_font": primary,
        "correlated_tim_uploads": correlated,
    }
    report_path = project / "work/analysis/gpu_upload_coordinates.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
