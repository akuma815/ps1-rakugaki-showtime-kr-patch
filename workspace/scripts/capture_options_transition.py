#!/usr/bin/env python3
"""Burst-capture the RAM region containing font primitives during Options entry."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from capture_duckstation_ram import RAM_BASE, RAM_SIZE, connect_with_retry
from capture_ui_ram_stages import sha256, wait_for_new_screenshot


EXPECTED_SETTINGS_SHA256 = "02037cc124559e370bdf7dea165806c272ee97b835fcd61bfa732567219e9c9d"
EXPECTED_BIN_SHA256 = "99dc6c7f748c4882d6f197a0a0a2eb943affc25bb4271deff9bb9bf55859aa54"
EXPECTED_CUE_SHA256 = "4c6d97218e38fb592e9c426bf2fa086a2110005a82ab062352387594e0af3f97"
BURST_BASE = 0x80080000
BURST_SIZE = 0x80000


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--duckstation", type=Path, default=Path(r"H:\Duckstation"))
    parser.add_argument("--session-id", default="options_transition_001")
    parser.add_argument("--samples", type=int, default=32)
    parser.add_argument("--interval", type=float, default=0.05)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--port", type=int, default=2345)
    args = parser.parse_args()

    project = args.project.resolve()
    duckstation = args.duckstation.resolve()
    exe = duckstation / "duckstation-qt-x64-ReleaseLTCG.exe"
    cue = duckstation / "Roms/Rakugaki Showtime (J)/Rakugaki Showtime (Japan).cue"
    disc_bin = cue.with_suffix(".bin")
    settings = duckstation / "settings.ini"
    screenshots_root = duckstation / "screenshots"
    output_root = project / "work/runtime" / args.session_id
    report_path = project / "work/analysis" / f"{args.session_id}.json"
    if output_root.exists() or report_path.exists():
        raise FileExistsError(f"refusing to overwrite existing session: {args.session_id}")
    actual_hashes = {"settings": sha256(settings), "disc_bin": sha256(disc_bin), "disc_cue": sha256(cue)}
    expected_hashes = {"settings": EXPECTED_SETTINGS_SHA256, "disc_bin": EXPECTED_BIN_SHA256, "disc_cue": EXPECTED_CUE_SHA256}
    if actual_hashes != expected_hashes:
        raise ValueError(f"baseline hash mismatch: expected={expected_hashes}, actual={actual_hashes}")

    baseline_screenshots = {path.resolve() for path in screenshots_root.glob("*.png")}
    output_root.mkdir(parents=True, exist_ok=False)
    process: subprocess.Popen[bytes] | None = None
    client = None
    captures = []
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        process = subprocess.Popen([str(exe), "-batch", "-fastboot", "--", str(cue)], cwd=duckstation)
        client = connect_with_retry(args.port, 20)
        supported = client.send_packet("qSupported:multiprocess+;swbreak+;hwbreak+")
        initial_status = client.send_packet("?")
        client.resume()
        print("메인 메뉴에서 Options를 선택한 상태로 F10을 누른 뒤 곧바로 결정 버튼을 누르세요.", flush=True)
        screenshot = wait_for_new_screenshot(screenshots_root, baseline_screenshots, set(), process, args.timeout)
        trigger_stop = client.interrupt()
        pre_ram = client.read_memory(RAM_BASE, RAM_SIZE)
        pre_path = output_root / "ram_pre_transition.bin"
        pre_path.write_bytes(pre_ram)
        screenshot_target = output_root / screenshot.name
        shutil.move(str(screenshot), str(screenshot_target))
        client.resume()
        burst_start = time.monotonic()
        for index in range(args.samples):
            target = burst_start + index * args.interval
            remaining = target - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            stop_reply = client.interrupt()
            payload = client.read_memory(BURST_BASE, BURST_SIZE)
            path = output_root / f"ram_burst_{index:02d}.bin"
            path.write_bytes(payload)
            captures.append(
                {
                    "index": index,
                    "elapsed_seconds": time.monotonic() - burst_start,
                    "stop_reply": stop_reply.decode("ascii", errors="replace"),
                    "ram": path.relative_to(project).as_posix(),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
            if index + 1 < args.samples:
                client.resume()
    finally:
        if client is not None:
            client.close()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    if sha256(settings) != EXPECTED_SETTINGS_SHA256:
        raise ValueError("DuckStation settings changed during capture")
    report = {
        "schema_version": 1,
        "observation_id": "HITL-OPTIONS-TRANSITION-001",
        "status": "CAPTURED",
        "session_id": args.session_id,
        "started_at": started_at,
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "baseline_sha256": actual_hashes,
        "trigger": {
            "instruction": "Highlight Options, press F10, then immediately confirm.",
            "stop_reply": trigger_stop.decode("ascii", errors="replace"),
            "screenshot": screenshot_target.relative_to(project).as_posix(),
            "screenshot_sha256": sha256(screenshot_target),
            "pre_transition_ram": pre_path.relative_to(project).as_posix(),
            "pre_transition_ram_sha256": hashlib.sha256(pre_ram).hexdigest(),
        },
        "burst": {
            "base": f"0x{BURST_BASE:08X}",
            "size": BURST_SIZE,
            "requested_interval_seconds": args.interval,
            "sample_count": len(captures),
            "captures": captures,
        },
        "gdb_server": {"host": "127.0.0.1", "port": args.port, "qSupported": (supported or b"").decode("ascii", errors="replace"), "initial_status": (initial_status or b"").decode("ascii", errors="replace")},
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(report_path, flush=True)


if __name__ == "__main__":
    main()
