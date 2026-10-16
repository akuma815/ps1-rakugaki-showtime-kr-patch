#!/usr/bin/env python3
"""Capture title and Options RAM snapshots when the user presses F10."""

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


EXPECTED_SETTINGS_SHA256 = "02037cc124559e370bdf7dea165806c272ee97b835fcd61bfa732567219e9c9d"
EXPECTED_BIN_SHA256 = "99dc6c7f748c4882d6f197a0a0a2eb943affc25bb4271deff9bb9bf55859aa54"
EXPECTED_CUE_SHA256 = "4c6d97218e38fb592e9c426bf2fa086a2110005a82ab062352387594e0af3f97"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wait_for_new_screenshot(root: Path, baseline: set[Path], consumed: set[Path], process: subprocess.Popen[bytes], timeout: float) -> Path:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"DuckStation exited before the requested screenshot: {process.returncode}")
        candidates = sorted(
            (path for path in root.glob("*.png") if path.resolve() not in baseline and path.resolve() not in consumed),
            key=lambda path: path.stat().st_mtime_ns,
        )
        if candidates:
            path = candidates[0]
            previous_size = -1
            for _ in range(20):
                size = path.stat().st_size
                if size > 0 and size == previous_size:
                    return path
                previous_size = size
                time.sleep(0.1)
            return path
        time.sleep(0.1)
    raise TimeoutError("timed out waiting for F10 screenshot")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--duckstation", type=Path, default=Path(r"H:\Duckstation"))
    parser.add_argument("--session-id", default="ui_ram_session_001")
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
    consumed: set[Path] = set()
    output_root.mkdir(parents=True, exist_ok=False)
    (output_root / "screenshots").mkdir()
    process: subprocess.Popen[bytes] | None = None
    client = None
    records = []
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        process = subprocess.Popen([str(exe), "-batch", "-fastboot", "--", str(cue)], cwd=duckstation)
        client = connect_with_retry(args.port, 20)
        supported = client.send_packet("qSupported:multiprocess+;swbreak+;hwbreak+")
        initial_status = client.send_packet("?")
        client.resume()

        for index, (stage, instruction) in enumerate(
            [
                ("title", "타이틀 메뉴가 보이면 F10을 한 번 누르세요."),
                ("options", "Options 화면으로 들어간 뒤 F10을 한 번 더 누르세요."),
            ]
        ):
            print(instruction, flush=True)
            screenshot = wait_for_new_screenshot(screenshots_root, baseline_screenshots, consumed, process, args.timeout)
            consumed.add(screenshot.resolve())
            stop_reply = client.interrupt()
            registers = client.send_packet("g") or b""
            ram = client.read_memory(RAM_BASE, RAM_SIZE)
            ram_path = output_root / f"ram_{stage}.bin"
            ram_path.write_bytes(ram)
            screenshot_target = output_root / "screenshots" / screenshot.name
            shutil.move(str(screenshot), str(screenshot_target))
            records.append(
                {
                    "stage": stage,
                    "trigger": "F10 screenshot",
                    "stop_reply": stop_reply.decode("ascii", errors="replace"),
                    "register_packet_sha256": hashlib.sha256(registers).hexdigest(),
                    "ram": ram_path.relative_to(project).as_posix(),
                    "ram_sha256": hashlib.sha256(ram).hexdigest(),
                    "screenshot": screenshot_target.relative_to(project).as_posix(),
                    "screenshot_sha256": sha256(screenshot_target),
                }
            )
            print(f"{stage} RAM 캡처 완료", flush=True)
            if index == 0:
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
        "observation_id": "HITL-FONT-CODE-001",
        "status": "CAPTURED" if len(records) == 2 else "INCOMPLETE",
        "session_id": args.session_id,
        "started_at": started_at,
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "baseline_sha256": actual_hashes,
        "gdb_server": {
            "host": "127.0.0.1",
            "port": args.port,
            "qSupported": (supported or b"").decode("ascii", errors="replace"),
            "initial_status": (initial_status or b"").decode("ascii", errors="replace"),
        },
        "captures": records,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(report_path, flush=True)


if __name__ == "__main__":
    main()
