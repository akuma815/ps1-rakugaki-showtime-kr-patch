#!/usr/bin/env python3
"""Screenshot-only HITL sweep of the Korean build (no CPU intrusion).

Unlike the VRAM occupancy probes this never halts the emulator, so the player
sees no stutter; it just grabs the DuckStation window on a fixed interval and
records hashes so text screens can be reviewed afterwards.
"""

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
from capture_two_bank_poc import capture_window


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--duckstation", type=Path, default=Path(r"H:\Duckstation"))
    parser.add_argument("--session", default="001")
    parser.add_argument("--duration", type=float, default=900.0)
    parser.add_argument("--interval", type=float, default=4.0)
    args = parser.parse_args()
    project = args.project.resolve()
    duckstation = args.duckstation.resolve()
    exe = duckstation / "duckstation-qt-x64-ReleaseLTCG.exe"
    build = json.loads((project / "work/analysis/hangul_atlas_001_build.json").read_text(encoding="utf-8"))
    cue = project / build["output"]["cue"]
    disc_bin = project / build["output"]["bin"]
    if sha256_file(cue) != build["output"]["cue_sha256"] or sha256_file(disc_bin) != build["output"]["bin_sha256"]:
        raise ValueError("Korean build hash mismatch")

    output_dir = project / "work/runtime" / f"hangul_sweep_{args.session}"
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite: {output_dir}")
    output_dir.mkdir(parents=True)

    process = subprocess.Popen([str(exe), "-batch", "-fastboot", "--", str(cue)], cwd=duckstation)
    shots = []
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        start = time.monotonic()
        index = 0
        previous = None
        while time.monotonic() - start < args.duration:
            time.sleep(args.interval)
            target = output_dir / f"shot_{index:04d}.png"
            try:
                if not capture_window(process.pid, target):
                    index += 1
                    continue
            except RuntimeError:
                # The player closed the emulator window; keep what we captured.
                break
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if digest == previous:
                target.unlink()          # drop consecutive duplicates (paused/idle screens)
            else:
                shots.append({"index": index, "seconds": round(time.monotonic() - start, 1),
                              "file": target.name, "sha256": digest})
                previous = digest
            index += 1
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
        "role": "HITL text sweep",
        "status": "CAPTURED_REVIEW_PENDING",
        "session": args.session,
        "started_at": started_at,
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "disc": {"bin_sha256": build["output"]["bin_sha256"]},
        "interval_seconds": args.interval,
        "kept_shots": len(shots),
        "shots": shots,
    }
    report_path = project / "work/analysis" / f"hangul_sweep_{args.session}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "kept_shots": len(shots)}, indent=2))


if __name__ == "__main__":
    main()
