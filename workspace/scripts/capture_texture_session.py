#!/usr/bin/env python3
"""Dump every texture the game samples while the user plays (HITL, GUI on).

DuckStation writes one PNG per uploaded texture, its filename encoding the
palette and pixel size. Running this across a full play session captures every
on-screen text graphic (banners, in-game UI, effect words, option values) with
its exact bytes, which we then match to the disc for translation.
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from PIL import Image


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# DuckStation re-serialises settings.ini on exit, so its hash changes even when
# nothing meaningful did. Verify the values that actually matter instead.
CRITICAL_SETTINGS = {
    "Renderer": "Vulkan",
    "ResolutionScale": "9",
    "Multisamples": "8",
    "PGXPEnable": "true",
    "PGXPTextureCorrection": "true",
    "TextureFilter": "MMPXEnhanced",
    "DumpTextures": "false",
    "DumpCPUToVRAMCopies": "false",
    "EnableGDBServer": "true",
    "GDBServerPort": "2345",
}


def verify_settings(raw: bytes) -> None:
    parser = configparser.ConfigParser(strict=False)
    parser.read_string(raw.decode("utf-8"))
    for key, want in CRITICAL_SETTINGS.items():
        found = None
        for section in parser.sections():
            if parser.has_option(section, key):
                found = parser.get(section, key)
                break
        if found != want:
            raise ValueError(f"DuckStation setting {key} is {found!r}, expected {want!r}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--duckstation", type=Path, default=Path(r"H:\Duckstation"))
    parser.add_argument("--session", default="001")
    parser.add_argument("--duration", type=float, default=900.0)
    args = parser.parse_args()
    project = args.project.resolve()
    duckstation = args.duckstation.resolve()
    exe = duckstation / "duckstation-qt-x64-ReleaseLTCG.exe"
    build = json.loads((project / "work/analysis/hangul_atlas_001_build.json").read_text(encoding="utf-8"))
    cue = project / build["output"]["cue"]
    disc_bin = project / build["output"]["bin"]
    for path, want in [(cue, build["output"]["cue_sha256"]), (disc_bin, build["output"]["bin_sha256"])]:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        if digest.hexdigest() != want:
            raise ValueError(f"build hash mismatch: {path}")

    settings_path = duckstation / "settings.ini"
    # Only ever touch this game's own dump folder. A recursive sweep of the
    # textures root also picks up other games' replacement packs.
    textures_root = duckstation / "textures" / "SLPM-86272" / "dumps"
    settings_before = settings_path.read_bytes()
    settings_hash = hashlib.sha256(settings_before).hexdigest()
    verify_settings(settings_before)
    text = settings_before.decode("utf-8")
    replacements = {
        "DumpTextures = false": "DumpTextures = true",
        "DumpTextureForceAlphaChannel = false": "DumpTextureForceAlphaChannel = true",
        "DumpTextureWidthThreshold = 16": "DumpTextureWidthThreshold = 1",
        "DumpTextureHeightThreshold = 16": "DumpTextureHeightThreshold = 1",
    }
    for old, new in replacements.items():
        if text.count(old) != 1:
            raise ValueError(f"expected one settings line: {old}")
        text = text.replace(old, new)
    existing = {path.resolve() for path in textures_root.glob("*.png")} if textures_root.exists() else set()

    output_dir = project / "work/runtime" / f"texture_session_{args.session}"
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite: {output_dir}")

    process = None
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        settings_path.write_bytes(text.encode("utf-8"))
        # GUI on so the player can drive the game; -fastboot to skip BIOS logo.
        command = [str(exe), "-batch", "-fastboot", "--", str(cue)]
        process = subprocess.Popen(command, cwd=duckstation)
        time.sleep(args.duration)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        settings_path.write_bytes(settings_before)
    verify_settings(settings_path.read_bytes())

    created = sorted(path for path in textures_root.glob("*.png")
                     if path.is_file() and path.resolve() not in existing)
    output_dir.mkdir(parents=True)
    records = []
    for source in created:
        destination = output_dir / source.name
        if destination.exists():
            continue
        shutil.move(str(source), str(destination))
        try:
            with Image.open(destination) as image:
                records.append({"file": destination.name, "width": image.width, "height": image.height})
        except OSError:
            records.append({"file": destination.name, "non_image": True})

    report = {
        "schema_version": 1,
        "observation_id": "MENU-GRAPHICS-001",
        "role": f"texture dump session ({args.session})",
        "status": "CAPTURED",
        "started_at": started_at,
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "disc_bin_sha256": build["output"]["bin_sha256"],
        "dump_count": len(records),
        "records": records,
    }
    report_path = project / "work/analysis" / f"texture_session_{args.session}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "dump_count": len(records)}, indent=2))


if __name__ == "__main__":
    main()
