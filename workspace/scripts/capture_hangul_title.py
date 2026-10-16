#!/usr/bin/env python3
"""Capture full-viewport title screenshots of the FONT-TWO-BANK-POC-002 build."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from build_two_bank_poc import sha256_file
from capture_two_bank_poc import capture_window, position_window


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--duckstation", type=Path, default=Path(r"H:\Duckstation"))
    parser.add_argument("--first-shot", type=float, default=28.0)
    parser.add_argument("--shots", type=int, default=16)
    parser.add_argument("--interval", type=float, default=1.5)
    args = parser.parse_args()
    project = args.project.resolve()
    duckstation = args.duckstation.resolve()
    exe = duckstation / "duckstation-qt-x64-ReleaseLTCG.exe"
    build = json.loads((project / "work/analysis/hangul_atlas_001_build.json").read_text(encoding="utf-8"))
    cue = project / build["output"]["cue"]
    disc_bin = project / build["output"]["bin"]
    if sha256_file(cue) != build["output"]["cue_sha256"] or sha256_file(disc_bin) != build["output"]["bin_sha256"]:
        raise ValueError("PoC build hash mismatch")

    output_dir = project / "work/runtime/hangul_atlas_001_title"
    output_dir.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen([str(exe), "-batch", "-fastboot", "--", str(cue)], cwd=duckstation)
    shots = []
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        position_window(process.pid)
        start = time.monotonic()
        remaining = args.first_shot - (time.monotonic() - start)
        if remaining > 0:
            time.sleep(remaining)
        for index in range(args.shots):
            target = output_dir / f"title_{index:03d}.png"
            capture_window(process.pid, target)
            shots.append({
                "index": index,
                "seconds": round(time.monotonic() - start, 2),
                "file": target.relative_to(project).as_posix(),
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            })
            time.sleep(args.interval)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    report = {
        "schema_version": 1,
        "observation_id": "HANGUL-ATLAS-001",
        "role": "full-viewport title visual capture",
        "status": "CAPTURED_REVIEW_PENDING",
        "started_at": started_at,
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "disc": {"bin_sha256": build["output"]["bin_sha256"], "cue_sha256": build["output"]["cue_sha256"]},
        "shots": shots,
    }
    report_path = project / "work/analysis/hangul_atlas_001_title_capture.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "shots": len(shots)}, indent=2))


if __name__ == "__main__":
    main()
