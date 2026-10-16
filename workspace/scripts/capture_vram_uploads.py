#!/usr/bin/env python3
"""Temporarily enable DuckStation CPU-to-VRAM dumps and match the font TIM upload."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import subprocess
import time
from pathlib import Path

from PIL import Image

from psx_tim import parse_tim


def psx_rgb(value: int) -> tuple[int, int, int]:
    return (
        (value & 0x1F) * 255 // 31,
        ((value >> 5) & 0x1F) * 255 // 31,
        ((value >> 10) & 0x1F) * 255 // 31,
    )


def rgb555_sha256(image: Image.Image) -> str:
    output = bytearray()
    for red, green, blue in image.convert("RGB").get_flattened_data():
        value = round(red * 31 / 255) | (round(green * 31 / 255) << 5) | (round(blue * 31 / 255) << 10)
        output.extend(struct.pack("<H", value))
    return hashlib.sha256(output).hexdigest()


def payload_rgb555_sha256(payload: bytes) -> str:
    values = struct.unpack(f"<{len(payload) // 2}H", payload)
    normalized = struct.pack(f"<{len(values)}H", *(value & 0x7FFF for value in values))
    return hashlib.sha256(normalized).hexdigest()


def expected_payload_image(payload: bytes, width_words: int, height: int) -> Image.Image:
    values = struct.unpack(f"<{len(payload) // 2}H", payload)
    image = Image.new("RGB", (width_words, height))
    image.putdata([psx_rgb(value) for value in values])
    return image


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
    settings_before = settings_path.read_bytes()
    settings_hash = hashlib.sha256(settings_before).hexdigest()
    settings_text = settings_before.decode("utf-8")
    old_line = "DumpCPUToVRAMCopies = false"
    if settings_text.count(old_line) != 1:
        raise ValueError("expected one disabled DumpCPUToVRAMCopies setting")
    settings_after = settings_text.replace(old_line, "DumpCPUToVRAMCopies = true").encode("utf-8")

    existing = {path.name for path in duckstation.glob("cpu_to_vram_copy_*.png")}
    process = None
    try:
        settings_path.write_bytes(settings_after)
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

    created = sorted((path for path in duckstation.glob("cpu_to_vram_copy_*.png") if path.name not in existing), key=lambda p: int(p.stem.rsplit("_", 1)[1]))
    output_dir = project / "work/runtime/vram_uploads"
    output_dir.mkdir(parents=True, exist_ok=True)
    moved = []
    for source in created:
        destination = output_dir / source.name
        if destination.exists():
            raise ValueError(f"refusing to overwrite prior capture: {destination}")
        shutil.move(str(source), str(destination))
        moved.append(destination)

    targets = json.loads((project / "workspace/config/asset_targets.json").read_text(encoding="utf-8"))
    offset = targets["targets"]["primary_font_atlas"]["container_offset"]
    hagecho = (project / "extracted/HAGECHO.BIN").read_bytes()
    tim = parse_tim(hagecho, offset)
    if tim is None:
        raise ValueError("font target no longer parses")
    image_meta = tim["image"]
    payload = hagecho[image_meta["payload_offset"] : image_meta["payload_offset"] + image_meta["payload_size"]]
    expected_image = expected_payload_image(payload, image_meta["width_words"], image_meta["height"])
    expected_hash = payload_rgb555_sha256(payload)
    expected_path = project / "work/graphics/font_candidate_005/font_payload_words.png"
    expected_image.save(expected_path)

    records = []
    matches = []
    for path in moved:
        with Image.open(path) as image:
            record = {
                "file": path.relative_to(project).as_posix(),
                "width": image.width,
                "height": image.height,
                "rgb555_sha256": rgb555_sha256(image),
            }
        records.append(record)
        if record["width"] == image_meta["width_words"] and record["height"] == image_meta["height"] and record["rgb555_sha256"] == expected_hash:
            matches.append(record)

    report = {
        "schema_version": 1,
        "status": "MATCHED" if matches else "NO_EXACT_MATCH",
        "duckstation_version": "0.1.11636",
        "capture_seconds": args.seconds,
        "settings_restored": True,
        "settings_sha256": settings_hash,
        "created_dump_count": len(records),
        "font_target": {
            "source": "HAGECHO.BIN",
            "container_offset": offset,
            "destination_from_tim": {"x_words": image_meta["x"], "y": image_meta["y"]},
            "upload_dimensions_words": {"width": image_meta["width_words"], "height": image_meta["height"]},
            "payload_rgb555_sha256": expected_hash,
            "expected_preview": expected_path.relative_to(project).as_posix(),
        },
        "exact_matches": matches,
        "uploads": records,
    }
    report_path = project / "work/analysis/duckstation_vram_uploads.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
