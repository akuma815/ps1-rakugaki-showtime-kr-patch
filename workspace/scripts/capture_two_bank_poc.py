#!/usr/bin/env python3
"""Fresh-boot and automatically capture the title-only two-bank PoC."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from PIL import ImageGrab


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


EXPECTED_SETTINGS_SHA256 = "02037cc124559e370bdf7dea165806c272ee97b835fcd61bfa732567219e9c9d"
VK_F10 = 0x79
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_window(pid: int) -> int:
    result: list[int] = []
    user32 = ctypes.windll.user32
    enum_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd: int, _lparam: int) -> bool:
        window_pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if window_pid.value == pid and user32.IsWindowVisible(hwnd):
            result.append(hwnd)
        return True

    user32.EnumWindows(enum_type(callback), 0)
    return result[0] if result else 0


def press_f10(pid: int) -> None:
    user32 = ctypes.windll.user32
    hwnd = find_window(pid)
    if not hwnd:
        raise RuntimeError("DuckStation window not found")
    user32.ShowWindow(hwnd, 5)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.15)
    command = (
        "$shell = New-Object -ComObject WScript.Shell; "
        f"if (-not $shell.AppActivate({pid})) {{ exit 2 }}; "
        "Start-Sleep -Milliseconds 250; $shell.SendKeys('{F10}')"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"WScript F10 delivery failed: {completed.returncode}")


class WindowNotForeground(RuntimeError):
    """DuckStation is not the foreground window, so a grab would capture something else."""


def capture_window(pid: int, target: Path, *, require_foreground: bool = True) -> None:
    """Save the DuckStation window region to `target`.

    This grabs a screen *region*, not the window's back buffer, so whatever sits
    on top of that region is what gets saved. One run captured the user's
    browser this way. The foreground check makes that impossible; callers that
    genuinely want the region regardless can pass require_foreground=False.
    """
    user32 = ctypes.windll.user32
    hwnd = find_window(pid)
    if not hwnd:
        raise RuntimeError("DuckStation window not found")
    if require_foreground and user32.GetForegroundWindow() != hwnd:
        raise WindowNotForeground("DuckStation window is not in front; refusing to capture the screen behind it")
    rectangle = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rectangle)):
        raise RuntimeError("DuckStation window rectangle unavailable")
    if rectangle.right <= rectangle.left or rectangle.bottom <= rectangle.top:
        raise RuntimeError("DuckStation window rectangle is empty")
    image = ImageGrab.grab(bbox=(rectangle.left, rectangle.top, rectangle.right, rectangle.bottom), all_screens=True)
    image.save(target)


def position_window(pid: int) -> None:
    user32 = ctypes.windll.user32
    deadline = time.monotonic() + 5.0
    hwnd = 0
    while time.monotonic() < deadline and not hwnd:
        hwnd = find_window(pid)
        if not hwnd:
            time.sleep(0.1)
    if not hwnd or not user32.MoveWindow(hwnd, 0, 0, 1000, 1000, True):
        raise RuntimeError("DuckStation window could not be positioned for full-viewport capture")


def wait_new_png(root: Path, baseline: set[Path], timeout: float = 5.0) -> Path:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        candidates = sorted(
            (path for path in root.glob("*.png") if path.resolve() not in baseline),
            key=lambda path: path.stat().st_mtime_ns,
        )
        if candidates:
            candidate = candidates[-1]
            previous = -1
            for _ in range(20):
                size = candidate.stat().st_size
                if size > 0 and size == previous:
                    return candidate
                previous = size
                time.sleep(0.1)
            return candidate
        time.sleep(0.1)
    raise TimeoutError("F10 screenshot was not created")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--duckstation", type=Path, default=Path(r"H:\Duckstation"))
    parser.add_argument("--session-id", default="font_two_bank_poc_001_runtime")
    parser.add_argument("--times", default="10,15,20,25,30,35,40")
    args = parser.parse_args()
    project = args.project.resolve()
    duckstation = args.duckstation.resolve()
    exe = duckstation / "duckstation-qt-x64-ReleaseLTCG.exe"
    settings = duckstation / "settings.ini"
    screenshots = duckstation / "screenshots"
    build = json.loads((project / "work/analysis/font_two_bank_poc_001_build.json").read_text(encoding="utf-8"))
    cue = project / build["output"]["cue"]
    disc_bin = project / build["output"]["bin"]
    output_root = project / "work/runtime" / args.session_id
    report_path = project / "work/analysis" / f"{args.session_id}.json"
    if output_root.exists() or report_path.exists():
        raise FileExistsError(f"refusing to overwrite existing session: {args.session_id}")
    if sha256(settings) != EXPECTED_SETTINGS_SHA256:
        raise ValueError("DuckStation settings baseline mismatch")
    if sha256(cue) != build["output"]["cue_sha256"] or sha256(disc_bin) != build["output"]["bin_sha256"]:
        raise ValueError("two-bank PoC build hash mismatch")
    schedule = [float(value) for value in args.times.split(",")]
    if schedule != sorted(schedule) or not schedule:
        raise ValueError("capture times must be a nonempty ascending list")

    output_root.mkdir(parents=True, exist_ok=False)
    capture_dir = output_root / "screenshots"
    capture_dir.mkdir()
    records: list[dict] = []
    process: subprocess.Popen[bytes] | None = None
    error: str | None = None
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    start = time.monotonic()
    try:
        process = subprocess.Popen([str(exe), "-batch", "-fastboot", "--", str(cue)], cwd=duckstation)
        position_window(process.pid)
        for target_time in schedule:
            remaining = start + target_time - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            if process.poll() is not None:
                raise RuntimeError(f"DuckStation exited early: {process.returncode}")
            target = capture_dir / f"t{target_time:05.1f}.png"
            capture_window(process.pid, target)
            records.append({
                "elapsed_seconds": target_time,
                "screenshot": target.relative_to(project).as_posix(),
                "sha256": sha256(target),
                "capture_method": "Win32 window rectangle via PIL ImageGrab",
            })
            print(f"captured t={target_time:.1f}s", flush=True)
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
            "observation_id": "FONT-TWO-BANK-POC-001",
            "status": "CAPTURED_PENDING_REVIEW" if len(records) == len(schedule) and error is None else "INCOMPLETE",
            "session_id": args.session_id,
            "started_at": started_at,
            "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "baseline": {
                "build_bin_sha256": build["output"]["bin_sha256"],
                "build_cue_sha256": build["output"]["cue_sha256"],
                "settings_sha256": EXPECTED_SETTINGS_SHA256,
            },
            "captures": records,
            "error": error,
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if sha256(settings) != EXPECTED_SETTINGS_SHA256:
        raise ValueError("DuckStation settings changed during capture")
    print(report_path)


if __name__ == "__main__":
    main()
