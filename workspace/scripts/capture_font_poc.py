#!/usr/bin/env python3
"""Capture the FONT-VISIBILITY-001 title/menu/return runtime gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


EXPECTED_SETTINGS_SHA256 = "02037cc124559e370bdf7dea165806c272ee97b835fcd61bfa732567219e9c9d"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wait_for_screenshot(
    root: Path,
    baseline: set[Path],
    consumed: set[Path],
    process: subprocess.Popen[bytes],
    timeout: float,
) -> Path:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"DuckStation exited early: {process.returncode}")
        candidates = sorted(
            (path for path in root.glob("*.png") if path.resolve() not in baseline and path.resolve() not in consumed),
            key=lambda path: path.stat().st_mtime_ns,
        )
        if candidates:
            path = candidates[0]
            previous = -1
            for _ in range(20):
                size = path.stat().st_size
                if size > 0 and size == previous:
                    return path
                previous = size
                time.sleep(0.1)
            return path
        time.sleep(0.1)
    raise TimeoutError("timed out waiting for F10 screenshot")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--duckstation", type=Path, default=Path(r"H:\Duckstation"))
    parser.add_argument("--session-id", default="font_poc_001_runtime")
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args()

    project = args.project.resolve()
    duckstation = args.duckstation.resolve()
    exe = duckstation / "duckstation-qt-x64-ReleaseLTCG.exe"
    settings = duckstation / "settings.ini"
    screenshots_root = duckstation / "screenshots"
    build_report = json.loads((project / "work/analysis/font_poc_001_build.json").read_text(encoding="utf-8"))
    cue = project / build_report["output"]["cue"]
    disc_bin = project / build_report["output"]["bin"]
    output_root = project / "work/runtime" / args.session_id
    report_path = project / "work/analysis" / f"{args.session_id}.json"
    if output_root.exists() or report_path.exists():
        raise FileExistsError(f"refusing to overwrite existing session: {args.session_id}")
    if sha256(settings) != EXPECTED_SETTINGS_SHA256:
        raise ValueError("DuckStation settings baseline mismatch")
    if sha256(disc_bin) != build_report["output"]["bin_sha256"] or sha256(cue) != build_report["output"]["cue_sha256"]:
        raise ValueError("FONT-POC-001 build hash mismatch")

    baseline = {path.resolve() for path in screenshots_root.glob("*.png")}
    consumed: set[Path] = set()
    output_root.mkdir(parents=True, exist_ok=False)
    (output_root / "screenshots").mkdir()
    records: list[dict] = []
    process: subprocess.Popen[bytes] | None = None
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    error: str | None = None
    try:
        process = subprocess.Popen([str(exe), "-batch", "-fastboot", "--", str(cue)], cwd=duckstation)
        stages = [
            ("title", "타이틀의 PUSH START 위치가 보이면 F10을 누르세요."),
            ("mode_select", "버튼을 눌러 Mode Select에 진입한 뒤 F10을 누르세요."),
        ]
        for stage, instruction in stages:
            print(instruction, flush=True)
            screenshot = wait_for_screenshot(screenshots_root, baseline, consumed, process, args.timeout)
            consumed.add(screenshot.resolve())
            target = output_root / "screenshots" / screenshot.name
            shutil.move(str(screenshot), str(target))
            records.append({
                "stage": stage,
                "trigger": "F10 screenshot",
                "screenshot": target.relative_to(project).as_posix(),
                "screenshot_sha256": sha256(target),
            })
            print(f"{stage} 캡처 완료", flush=True)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        report = {
            "schema_version": 1,
            "observation_id": "FONT-VISIBILITY-001",
            "status": "CAPTURED_PENDING_REVIEW" if len(records) == 2 and error is None else "INCOMPLETE",
            "session_id": args.session_id,
            "started_at": started_at,
            "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "baseline": {
                "build_bin_sha256": build_report["output"]["bin_sha256"],
                "build_cue_sha256": build_report["output"]["cue_sha256"],
                "settings_sha256": EXPECTED_SETTINGS_SHA256,
            },
            "captures": records,
            "error": error,
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if sha256(settings) != EXPECTED_SETTINGS_SHA256:
        raise ValueError("DuckStation settings changed during runtime gate")
    print(report_path, flush=True)


if __name__ == "__main__":
    main()
