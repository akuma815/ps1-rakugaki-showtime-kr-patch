#!/usr/bin/env python3
"""Decode extracted PS1 STR movies to review MP4s and contact sheets.

The MP4 files are review proxies only.  Subtitle reinsertion must always start
from the immutable raw STR extraction recorded by movie_catalog_001.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


KNOWN_SOURCE_DECODE_ANOMALIES = {
    "GOOD": {
        "ffmpeg_proxy_missing_pts_frame": 1608,
        "raw_frame_number": 1609,
        "source_lba": 174716,
        "raw_frame_chunks_complete": True,
        "mode2_form1_edc_ecc_valid": True,
        "assessment": "FFmpeg 7.1 MDEC rejection; raw source damage is not established",
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_checked(command: list[str]) -> str:
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}\n{result.stdout}")
    return result.stdout


def make_contact_sheet(video: Path, target: Path, sample_count: int = 12) -> dict:
    frame_count, duration = imageio_ffmpeg.count_frames_and_secs(str(video))
    reader = imageio_ffmpeg.read_frames(str(video), pix_fmt="rgb24")
    metadata = next(reader)
    width, height = metadata["size"]
    fps = float(metadata["fps"])
    wanted = sorted({
        round(index * (frame_count - 1) / max(sample_count - 1, 1))
        for index in range(sample_count)
    })
    wanted_set = set(wanted)
    samples: list[tuple[int, Image.Image]] = []
    for frame_index, frame in enumerate(reader):
        if frame_index not in wanted_set:
            continue
        samples.append((frame_index, Image.frombytes("RGB", (width, height), frame)))
        if len(samples) == len(wanted):
            break

    columns = 3
    rows = (len(samples) + columns - 1) // columns
    label_height = 20
    sheet = Image.new("RGB", (columns * width, rows * (height + label_height)), "black")
    draw = ImageDraw.Draw(sheet)
    for index, (frame_index, image) in enumerate(samples):
        x = index % columns * width
        y = index // columns * (height + label_height)
        draw.text((x + 4, y + 3), f"{frame_index / fps:.2f}s / frame {frame_index}", fill="white")
        sheet.paste(image, (x, y + label_height))
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target)
    return {
        "frame_count": frame_count,
        "duration_seconds": round(duration, 6),
        "fps": fps,
        "width": width,
        "height": height,
        "path": target.as_posix(),
        "sha256": sha256_file(target),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--movie", action="append", help="decode only the named stem, e.g. BAD or OPEN")
    parser.add_argument("--crf", type=int, default=10)
    args = parser.parse_args()

    project = args.project.resolve()
    catalog_path = project / "work/analysis/movie_catalog_001.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    selected = {name.upper() for name in args.movie} if args.movie else None
    movies = [
        item for item in catalog["movies"]
        if selected is None or Path(item["disc_path"]).stem.upper() in selected
    ]
    if selected is not None:
        found = {Path(item["disc_path"]).stem.upper() for item in movies}
        missing = selected - found
        if missing:
            raise ValueError(f"movie names not found: {', '.join(sorted(missing))}")

    ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    ffmpeg_version = run_checked([str(ffmpeg), "-hide_banner", "-version"]).splitlines()[0]
    partial_suffix = "_".join(sorted(selected)) if selected is not None else None
    output_dir = project / (
        "work/video/movies/partial_review_mp4" if partial_suffix else "work/video/movies/review_mp4"
    )
    sheets_dir = project / (
        "work/video/movies/partial_contact_sheets" if partial_suffix else "work/video/movies/contact_sheets"
    )
    records = []
    for movie in movies:
        stem = Path(movie["disc_path"]).stem
        source = project / movie["output"]
        if sha256_file(source) != movie["raw_sha256"]:
            raise ValueError(f"raw STR hash mismatch: {source}")
        target = output_dir / f"{stem}.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        command = [
            str(ffmpeg), "-hide_banner", "-loglevel", "warning", "-y", "-threads", "1",
            "-f", "psxstr", "-i", str(source),
            "-map", "0:v:0", "-map", "0:a:0",
            "-vf", "scale=in_range=full:out_range=tv",
            "-c:v", "libx264", "-preset", "medium", "-crf", str(args.crf),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            str(target),
        ]
        encode_log = run_checked(command)
        run_checked([
            str(ffmpeg), "-hide_banner", "-loglevel", "error", "-i", str(target),
            "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "NUL",
        ])
        sheet = make_contact_sheet(target, sheets_dir / f"{stem}.png")
        expected_frames = movie["video"]["frame_count"]
        dropped_frames = expected_frames - sheet["frame_count"]
        source_decode_warnings = [
            line for line in encode_log.splitlines()
            if any(marker in line for marker in (
                "damaged",
                "Error submitting packet",
                "Invalid data found when processing input",
            ))
        ]
        if dropped_frames < 0:
            raise ValueError(
                f"decoded more frames than the raw STR contains for {stem}: "
                f"{sheet['frame_count']} > {expected_frames}"
            )
        if dropped_frames and not source_decode_warnings:
            raise ValueError(
                f"decoded frame count mismatch without a source warning for {stem}: "
                f"{sheet['frame_count']} != {expected_frames}"
            )
        if dropped_frames > 1:
            raise ValueError(
                f"too many source frames were rejected for {stem}: "
                f"{dropped_frames} of {expected_frames}"
            )
        expected_duration = movie["duration_seconds"]
        if abs(sheet["duration_seconds"] - expected_duration) > 1 / sheet["fps"] + 0.01:
            raise ValueError(
                f"decoded duration mismatch for {stem}: {sheet['duration_seconds']} != {expected_duration}"
            )
        records.append({
            "asset_id": movie["asset_id"],
            "source": movie["output"],
            "source_sha256": movie["raw_sha256"],
            "raw_frame_count": expected_frames,
            "decoded_frame_count": sheet["frame_count"],
            "dropped_source_frames": dropped_frames,
            "output": target.relative_to(project).as_posix(),
            "output_size": target.stat().st_size,
            "output_sha256": sha256_file(target),
            "contact_sheet": {
                **sheet,
                "path": (sheets_dir / f"{stem}.png").relative_to(project).as_posix(),
            },
            "source_decode_warnings": source_decode_warnings,
            "known_source_decode_anomaly": (
                KNOWN_SOURCE_DECODE_ANOMALIES.get(stem) if dropped_frames else None
            ),
            "ffmpeg_command": command[1:],
        })
        warning_suffix = f", {dropped_frames} source frame rejected" if dropped_frames else ""
        print(
            f"decoded {stem}: {sheet['frame_count']}/{expected_frames} frames, "
            f"{sheet['duration_seconds']:.3f}s{warning_suffix}"
        )

    report = {
        "schema_version": 1,
        "observation_id": "PSX-MOVIE-DECODE-001",
        "source_catalog": catalog_path.relative_to(project).as_posix(),
        "ffmpeg": {
            "path": str(ffmpeg),
            "sha256": sha256_file(ffmpeg),
            "version": ffmpeg_version,
            "imageio_ffmpeg_version": imageio_ffmpeg.__version__,
        },
        "proxy_policy": "review only; H.264 CRF 10 and AAC are never reinsertion inputs",
        "scope": "partial" if partial_suffix else "full_7_movies",
        "selected_movies": sorted(selected) if selected is not None else None,
        "movies": records,
        "status": (
            ("PARTIAL_" if partial_suffix else "")
            + "REVIEW_PROXIES_DECODED_WITH_SOURCE_WARNINGS_SUBTITLE_TIMING_PENDING"
            if any(item["source_decode_warnings"] for item in records)
            else ("PARTIAL_" if partial_suffix else "")
            + "REVIEW_PROXIES_DECODED_SUBTITLE_TIMING_PENDING"
        ),
    }
    report_name = f"movie_decode_partial_{partial_suffix}.json" if partial_suffix else "movie_decode_001.json"
    report_path = project / "work/analysis" / report_name
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "movies": len(records),
        "report": report_path.relative_to(project).as_posix(),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
