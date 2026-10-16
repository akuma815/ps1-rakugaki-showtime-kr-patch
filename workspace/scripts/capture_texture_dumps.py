#!/usr/bin/env python3
"""Temporarily capture textures actually sampled by Rakugaki Showtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

from PIL import Image, ImageDraw


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
    textures_root = duckstation / "textures"
    settings_before = settings_path.read_bytes()
    settings_hash = hashlib.sha256(settings_before).hexdigest()
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
    existing = {path.resolve() for path in textures_root.rglob("*") if path.is_file()}

    process = None
    try:
        settings_path.write_bytes(text.encode("utf-8"))
        command = [str(exe), "-batch", "-nogui", "-fastboot", "--", str(cue)]
        process = subprocess.Popen(command, cwd=duckstation, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        time.sleep(args.seconds)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        settings_path.write_bytes(settings_before)
    if hashlib.sha256(settings_path.read_bytes()).hexdigest() != settings_hash:
        raise ValueError("DuckStation settings restoration failed")

    created = sorted(path for path in textures_root.rglob("*") if path.is_file() and path.resolve() not in existing)
    run_tag = f"{args.seconds:03.0f}s"
    output_dir = project / "work/runtime" / f"texture_dumps_{run_tag}"
    output_dir.mkdir(parents=True, exist_ok=True)
    moved = []
    for source in created:
        destination = output_dir / source.name
        if destination.exists():
            raise ValueError(f"refusing to overwrite prior capture: {destination}")
        shutil.move(str(source), str(destination))
        moved.append(destination)

    records = []
    image_paths = []
    for path in moved:
        try:
            with Image.open(path) as image:
                records.append({"file": path.relative_to(project).as_posix(), "width": image.width, "height": image.height, "mode": image.mode})
                image_paths.append(path)
        except OSError:
            records.append({"file": path.relative_to(project).as_posix(), "non_image": True})

    cell_w, cell_h, cols = 260, 220, 4
    pages = []
    for page_index, start in enumerate(range(0, len(image_paths), 40)):
        batch = image_paths[start : start + 40]
        rows = (len(batch) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (24, 24, 24))
        draw = ImageDraw.Draw(sheet)
        for index, path in enumerate(batch):
            with Image.open(path) as source:
                image = source.convert("RGBA")
                image.thumbnail((240, 180), Image.Resampling.NEAREST)
                background = Image.new("RGBA", image.size, (64, 64, 64, 255))
                background.alpha_composite(image)
            col, row = index % cols, index // cols
            x, y = col * cell_w + 10, row * cell_h + 28
            sheet.paste(background.convert("RGB"), (x, y))
            draw.text((col * cell_w + 5, row * cell_h + 5), f"#{start + index:03d} {path.name[:28]}", fill=(255, 255, 255))
        sheet_path = project / "work/graphics" / f"texture_dump_{run_tag}_sheet_{page_index:02d}.png"
        sheet_path.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(sheet_path)
        pages.append(sheet_path.relative_to(project).as_posix())

    report = {
        "schema_version": 1,
        "status": "CAPTURED",
        "duckstation_version": "0.1.11636",
        "capture_seconds": args.seconds,
        "settings_restored": True,
        "settings_sha256": settings_hash,
        "dump_count": len(records),
        "contact_sheets": pages,
        "records": records,
    }
    report_path = project / "work/analysis" / f"duckstation_texture_dumps_{run_tag}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"dump_count={len(records)}")
    print(f"contact_sheets={len(pages)}")
    print(report_path)


if __name__ == "__main__":
    main()
