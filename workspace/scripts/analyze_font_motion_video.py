#!/usr/bin/env python3
"""Extract reproducible frame evidence from the user-supplied font motion video."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--sample-count", type=int, default=12)
    args = parser.parse_args()

    project = args.project.resolve()
    video = args.video.resolve()
    output_dir = project / "work/video/font_motion_001"
    report_path = project / "work/analysis/font_motion_001_video.json"
    output_dir.mkdir(parents=True, exist_ok=True)

    frame_count, seconds = imageio_ffmpeg.count_frames_and_secs(str(video))
    reader = imageio_ffmpeg.read_frames(str(video), pix_fmt="rgb24")
    metadata = next(reader)
    width, height = metadata["size"]
    fps = float(metadata["fps"])
    wanted = sorted({round(index * (frame_count - 1) / max(args.sample_count - 1, 1)) for index in range(args.sample_count)})
    samples: list[dict] = []
    images: list[Image.Image] = []
    wanted_set = set(wanted)
    for frame_index, frame in enumerate(reader):
        if frame_index not in wanted_set:
            continue
        image = Image.frombytes("RGB", (width, height), frame)
        target = output_dir / f"frame_{frame_index:06d}.png"
        image.save(target)
        samples.append({
            "frame": frame_index,
            "time_seconds": round(frame_index / fps, 6),
            "path": target.relative_to(project).as_posix(),
            "sha256": sha256(target),
        })
        images.append(image)
        if len(samples) == len(wanted):
            break

    thumb_width = 480
    thumb_height = round(height * thumb_width / width)
    columns = 3
    rows = (len(images) + columns - 1) // columns
    label_height = 24
    sheet = Image.new("RGB", (columns * thumb_width, rows * (thumb_height + label_height)), "black")
    draw = ImageDraw.Draw(sheet)
    for index, (image, sample) in enumerate(zip(images, samples)):
        x = (index % columns) * thumb_width
        y = (index // columns) * (thumb_height + label_height)
        sheet.paste(image.resize((thumb_width, thumb_height), Image.Resampling.LANCZOS), (x, y + label_height))
        draw.text((x + 6, y + 5), f"{sample['time_seconds']:.3f}s / frame {sample['frame']}", fill="white")
    sheet_path = output_dir / "contact_sheet.png"
    sheet.save(sheet_path)

    report = {
        "schema_version": 1,
        "source": {
            "path": str(video),
            "size": video.stat().st_size,
            "sha256": sha256(video),
        },
        "decoder": {
            "imageio_ffmpeg_version": imageio_ffmpeg.__version__,
            "ffmpeg_executable": imageio_ffmpeg.get_ffmpeg_exe(),
        },
        "video": {
            "frame_count": frame_count,
            "duration_seconds": seconds,
            "fps": fps,
            "width": width,
            "height": height,
            "codec": metadata.get("codec"),
        },
        "samples": samples,
        "contact_sheet": {
            "path": sheet_path.relative_to(project).as_posix(),
            "sha256": sha256(sheet_path),
        },
        "status": "FRAMES_EXTRACTED_PENDING_REVIEW",
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
