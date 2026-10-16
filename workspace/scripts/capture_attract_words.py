#!/usr/bin/env python3
"""Capture the attract demo densely to read the big sprite-font words (BIGFONT-SPRITE-001).

The 25-cell sprite font is already identified, so a word on screen names its own
cells: if the screen says スタート！ then the game drew the ス タ ー ト ！ cells,
in that order. The one thing still unknown is which words exist at all, and the
attract demo plays a full match unattended -- match start, hits, and the round
banner all go past without anyone touching a pad.

Frames are kept as PNGs and indexed; contact sheets come from a separate pass so
this stays a pure capture step that can be re-run without re-reading anything.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from build_two_bank_poc import sha256_file
from ctypes import wintypes

from capture_two_bank_poc import capture_window, find_window

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def fit_window_on_screen(pid: int) -> tuple[int, int, int, int]:
    """Place the emulator wholly inside the primary screen and return its rect.

    The first capture run lost the right half of every word because the window
    hung off the right edge of the screen: a region grab clips at the desktop
    bounds, so the missing pixels were never in the file. Positioning is
    verified rather than assumed.
    """
    user32 = ctypes.windll.user32
    screen_w, screen_h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    width, height = min(1280, screen_w - 8), min(1000, screen_h - 48)
    hwnd = 0
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and not hwnd:
        hwnd = find_window(pid)
        if not hwnd:
            time.sleep(0.1)
    if not hwnd or not user32.MoveWindow(hwnd, 0, 0, width, height, True):
        raise RuntimeError("emulator window could not be positioned")
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    if rect.right > screen_w or rect.bottom > screen_h:
        raise RuntimeError(f"window still off-screen: {rect.left},{rect.top},{rect.right},{rect.bottom} "
                           f"vs screen {screen_w}x{screen_h}")
    return rect.left, rect.top, rect.right, rect.bottom


def game_window_is_front(pid: int) -> bool:
    """True only when DuckStation's own window is the foreground window.

    capture_window grabs a screen *region*, not the window's back buffer, so
    anything the user brings over that region lands in the frame instead of the
    game. A previous run captured a browser this way. Skipping the shot is the
    right failure: a missing frame costs one sample, a wrong frame captures
    whatever the user happens to be doing.
    """
    hwnd = find_window(pid)
    return bool(hwnd) and ctypes.windll.user32.GetForegroundWindow() == hwnd


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--duckstation", type=Path, default=Path(r"H:\Duckstation"))
    parser.add_argument("--session", default="attract001")
    parser.add_argument("--build-report", default="work/analysis/hangul_atlas_001_build.json",
                        help="which build to boot; point at the markers build for BIGFONT-WORDS-002")
    parser.add_argument("--first-shot", type=float, default=20.0)
    parser.add_argument("--shots", type=int, default=240)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()
    project = args.project.resolve()
    duckstation = args.duckstation.resolve()
    exe = duckstation / "duckstation-qt-x64-ReleaseLTCG.exe"

    build = json.loads((project / args.build_report).read_text(encoding="utf-8"))
    cue = project / build["output"]["cue"]
    disc_bin = project / build["output"]["bin"]
    if sha256_file(cue) != build["output"]["cue_sha256"] or sha256_file(disc_bin) != build["output"]["bin_sha256"]:
        raise ValueError("build hash mismatch -- rebuild before capturing")

    output_dir = project / "work/runtime" / f"attract_words_{args.session}"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = project / "work/analysis" / f"attract_words_{args.session}.json"
    if report_path.exists():
        raise FileExistsError(f"refusing to overwrite: {report_path}")

    process = subprocess.Popen([str(exe), "-batch", "-fastboot", "--", str(cue)], cwd=duckstation)
    shots = []
    skipped = 0
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    status = "CAPTURED"
    try:
        rect = fit_window_on_screen(process.pid)
        print(f"window rect {rect}")
        start = time.monotonic()
        delay = args.first_shot - (time.monotonic() - start)
        if delay > 0:
            time.sleep(delay)
        for index in range(args.shots):
            if not game_window_is_front(process.pid):
                skipped += 1
                time.sleep(args.interval)
                continue
            target = output_dir / f"f{index:04d}.png"
            try:
                capture_window(process.pid, target)
            except Exception as error:  # emulator closed mid-run
                status = f"PARTIAL:{type(error).__name__}"
                break
            shots.append({"index": index, "seconds": round(time.monotonic() - start, 2),
                          "file": target.relative_to(project).as_posix(),
                          "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
            time.sleep(args.interval)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        report_path.write_text(json.dumps({
            "schema_version": 1,
            "observation_id": "BIGFONT-WORDS-001",
            "session": args.session,
            "status": status,
            "started_at": started_at,
            "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "bin_sha256": build["output"]["bin_sha256"],
            "frames": len(shots),
            "skipped_not_foreground": skipped,
            "shots": shots,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(json.dumps({"status": status, "frames": len(shots), "skipped": skipped,
                      "dir": output_dir.relative_to(project).as_posix()}, indent=2))


if __name__ == "__main__":
    main()
