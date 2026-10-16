#!/usr/bin/env python3
"""Capture an interactive DuckStation Options-screen evidence session safely.

The script changes dump settings only while DuckStation is running, restores the
exact original settings bytes in a finally block, and moves only files created
during this run into the project evidence directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path


EXPECTED_SETTINGS_SHA256 = "02037cc124559e370bdf7dea165806c272ee97b835fcd61bfa732567219e9c9d"
EXPECTED_BIN_SHA256 = "99dc6c7f748c4882d6f197a0a0a2eb943affc25bb4271deff9bb9bf55859aa54"
EXPECTED_CUE_SHA256 = "4c6d97218e38fb592e9c426bf2fa086a2110005a82ab062352387594e0af3f97"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def files_under(root: Path) -> set[Path]:
    if not root.exists():
        return set()
    return {path.resolve() for path in root.rglob("*") if path.is_file()}


def replace_once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise ValueError(f"expected exactly one settings line, found {count}: {old}")
    return text.replace(old, new)


def move_new_files(project: Path, destination: Path, sources: list[Path]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    destination.mkdir(parents=True, exist_ok=False)
    for index, source in enumerate(sources):
        target = destination / source.name
        if target.exists():
            target = destination / f"{index:04d}_{source.name}"
        shutil.move(str(source), str(target))
        records.append(
            {
                "file": target.relative_to(project).as_posix(),
                "size": target.stat().st_size,
                "sha256": sha256(target),
            }
        )
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--duckstation", type=Path, default=Path(r"H:\Duckstation"))
    parser.add_argument("--session-id", default="options_session_001")
    args = parser.parse_args()

    project = args.project.resolve()
    duckstation = args.duckstation.resolve()
    exe = duckstation / "duckstation-qt-x64-ReleaseLTCG.exe"
    cue = duckstation / "Roms/Rakugaki Showtime (J)/Rakugaki Showtime (Japan).cue"
    disc_bin = cue.with_suffix(".bin")
    settings_path = duckstation / "settings.ini"
    screenshots_root = duckstation / "screenshots"
    textures_root = duckstation / "textures"
    output_root = project / "work/runtime" / args.session_id
    report_path = project / "work/analysis" / f"{args.session_id}.json"

    if output_root.exists() or report_path.exists():
        raise FileExistsError(f"refusing to overwrite an existing session: {args.session_id}")
    for required in (exe, cue, disc_bin, settings_path):
        if not required.is_file():
            raise FileNotFoundError(required)

    actual_hashes = {
        "settings": sha256(settings_path),
        "disc_bin": sha256(disc_bin),
        "disc_cue": sha256(cue),
    }
    expected_hashes = {
        "settings": EXPECTED_SETTINGS_SHA256,
        "disc_bin": EXPECTED_BIN_SHA256,
        "disc_cue": EXPECTED_CUE_SHA256,
    }
    if actual_hashes != expected_hashes:
        raise ValueError(f"baseline hash mismatch: expected={expected_hashes}, actual={actual_hashes}")

    settings_before = settings_path.read_bytes()
    settings_text = settings_before.decode("utf-8")
    changes = {
        "DumpCPUToVRAMCopies = false": "DumpCPUToVRAMCopies = true",
        "DumpTextures = false": "DumpTextures = true",
        "DumpTextureForceAlphaChannel = false": "DumpTextureForceAlphaChannel = true",
        "DumpTextureWidthThreshold = 16": "DumpTextureWidthThreshold = 1",
        "DumpTextureHeightThreshold = 16": "DumpTextureHeightThreshold = 1",
    }
    for old, new in changes.items():
        settings_text = replace_once(settings_text, old, new)

    existing_uploads = {path.resolve() for path in duckstation.glob("cpu_to_vram_copy_*.png")}
    existing_screenshots = files_under(screenshots_root)
    existing_textures = files_under(textures_root)
    process: subprocess.Popen[bytes] | None = None
    exit_code: int | None = None
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")

    print("DuckStation이 열리면 타이틀의 Options 화면으로 이동하세요.", flush=True)
    print("Options 화면이 보이는 상태에서 F10을 한 번 누른 뒤 에뮬레이터 창을 닫으세요.", flush=True)
    try:
        settings_path.write_bytes(settings_text.encode("utf-8"))
        command = [str(exe), "-batch", "-fastboot", "--", str(cue)]
        process = subprocess.Popen(command, cwd=duckstation)
        exit_code = process.wait()
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        settings_path.write_bytes(settings_before)

    restored_hash = sha256(settings_path)
    if restored_hash != EXPECTED_SETTINGS_SHA256:
        raise ValueError(f"DuckStation settings restoration failed: {restored_hash}")

    new_uploads = sorted(
        (path for path in duckstation.glob("cpu_to_vram_copy_*.png") if path.resolve() not in existing_uploads),
        key=lambda path: path.stat().st_mtime_ns,
    )
    new_screenshots = sorted(files_under(screenshots_root) - existing_screenshots, key=lambda path: path.stat().st_mtime_ns)
    new_textures = sorted(files_under(textures_root) - existing_textures, key=lambda path: path.stat().st_mtime_ns)

    output_root.mkdir(parents=True, exist_ok=False)
    captures = {
        "vram_uploads": move_new_files(project, output_root / "vram_uploads", new_uploads),
        "screenshots": move_new_files(project, output_root / "screenshots", new_screenshots),
        "textures": move_new_files(project, output_root / "textures", new_textures),
    }
    report = {
        "schema_version": 1,
        "observation_id": "HITL-OPTIONS-001",
        "status": "CAPTURED" if captures["screenshots"] else "CAPTURED_WITHOUT_SCREENSHOT",
        "session_id": args.session_id,
        "started_at": started_at,
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "instructions": [
            "Start the verified original disc with fast boot.",
            "Open the title-screen Options menu.",
            "Press F10 once while Options is visible, then close DuckStation.",
        ],
        "duckstation_exit_code": exit_code,
        "baseline_sha256": actual_hashes,
        "settings_restored": True,
        "settings_restored_sha256": restored_hash,
        "capture_counts": {name: len(records) for name, records in captures.items()},
        "captures": captures,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["capture_counts"], ensure_ascii=False), flush=True)
    print(report_path, flush=True)


if __name__ == "__main__":
    main()
