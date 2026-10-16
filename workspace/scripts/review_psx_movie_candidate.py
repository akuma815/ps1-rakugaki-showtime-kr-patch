#!/usr/bin/env python3
"""Decode a merged PS1 STR candidate into a review MP4 and contact sheet."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg

from decode_psx_movies import make_contact_sheet, sha256_file


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def relative_path(path: Path, project: Path) -> str:
    try:
        return path.relative_to(project).as_posix()
    except ValueError:
        return str(path)


def run_checked(command: list[str]) -> str:
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}\n{result.stdout}")
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sheet", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--merge-report", type=Path)
    parser.add_argument("--expected-frames", type=int, required=True)
    parser.add_argument("--expected-duration", type=float, required=True)
    parser.add_argument("--crf", type=int, default=10)
    args = parser.parse_args()

    project = args.project.resolve()

    def resolve(path: Path) -> Path:
        return path if path.is_absolute() else project / path

    source = resolve(args.input)
    output = resolve(args.output)
    sheet_path = resolve(args.sheet)
    report_path = resolve(args.report)
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    command = [
        str(ffmpeg), "-hide_banner", "-loglevel", "warning", "-y", "-threads", "1",
        "-f", "psxstr", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0",
        "-vf", "scale=in_range=full:out_range=tv", "-c:v", "libx264", "-preset", "medium",
        "-crf", str(args.crf), "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-ar", "48000", "-movflags", "+faststart", str(output),
    ]
    encode_log = run_checked(command)
    run_checked([
        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-i", str(output),
        "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "NUL",
    ])
    sheet = make_contact_sheet(output, sheet_path, sample_count=12)
    if sheet["frame_count"] != args.expected_frames:
        raise ValueError(f"candidate frame mismatch: {sheet['frame_count']} != {args.expected_frames}")
    if abs(sheet["duration_seconds"] - args.expected_duration) > 1 / sheet["fps"] + 0.01:
        raise ValueError(
            f"candidate duration mismatch: {sheet['duration_seconds']} != {args.expected_duration}"
        )

    merge_report = None
    merge_report_path = None
    if args.merge_report is not None:
        merge_report_path = resolve(args.merge_report)
        merge_report = json.loads(merge_report_path.read_text(encoding="utf-8"))
        if merge_report["output_sha256"] != sha256_file(source):
            raise ValueError("candidate hash differs from merge report")
        if not merge_report["xa_byte_identical"]:
            raise ValueError("merge report does not prove byte-identical XA")

    report = {
        "schema_version": 1,
        "observation_id": "MOVIE-SUBTITLE-STR-POC-001-DECODE",
        "input": relative_path(source, project),
        "input_sha256": sha256_file(source),
        "merge_report": relative_path(merge_report_path, project) if merge_report_path else None,
        "merge_report_sha256": sha256_file(merge_report_path) if merge_report_path else None,
        "output": relative_path(output, project),
        "output_size": output.stat().st_size,
        "output_sha256": sha256_file(output),
        "contact_sheet": {
            **sheet,
            "path": relative_path(sheet_path, project),
        },
        "expected_frames": args.expected_frames,
        "expected_duration": args.expected_duration,
        "decode_warnings": [line for line in encode_log.splitlines() if line.strip()],
        "validation": {
            "full_video_audio_decode": True,
            "frame_count_matches": sheet["frame_count"] == args.expected_frames,
            "duration_matches": abs(sheet["duration_seconds"] - args.expected_duration) <= 1 / sheet["fps"] + 0.01,
            "xa_byte_identity_proven_by_merge_report": bool(merge_report and merge_report["xa_byte_identical"]),
        },
        "status": "CANDIDATE_REVIEW_DECODE_PASS_RUNTIME_NOT_BUILT",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "frames": sheet["frame_count"],
        "duration_seconds": sheet["duration_seconds"],
        "output": report["output"],
        "report": relative_path(report_path, project),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
