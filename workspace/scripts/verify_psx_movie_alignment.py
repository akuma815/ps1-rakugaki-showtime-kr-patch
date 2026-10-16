#!/usr/bin/env python3
"""Verify that a merged STR candidate retains source-frame alignment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import imageio_ffmpeg
import numpy as np

from decode_psx_movies import sha256_file


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def relative_path(path: Path, project: Path) -> str:
    try:
        return path.relative_to(project).as_posix()
    except ValueError:
        return str(path)


def load_rgb_frames(path: Path, input_params: list[str] | None = None) -> list[np.ndarray]:
    reader = imageio_ffmpeg.read_frames(
        str(path),
        pix_fmt="rgb24",
        input_params=input_params or [],
    )
    metadata = next(reader)
    width, height = metadata["size"]
    return [
        np.frombuffer(frame, np.uint8).reshape(height, width, 3)
        for frame in reader
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-frames", type=int, required=True)
    parser.add_argument("--sample-start", type=int, default=0)
    parser.add_argument("--sample-end", type=int)
    parser.add_argument("--max-shift", type=int, default=2)
    args = parser.parse_args()

    project = args.project.resolve()

    def resolve(path: Path) -> Path:
        return path if path.is_absolute() else project / path

    reference_path = resolve(args.reference)
    candidate_path = resolve(args.candidate)
    report_path = resolve(args.report)
    reference = load_rgb_frames(reference_path)
    candidate = load_rgb_frames(candidate_path, ["-f", "psxstr"])
    if len(reference) < args.expected_frames:
        raise ValueError(f"reference has too few frames: {len(reference)}")
    if len(candidate) != args.expected_frames:
        raise ValueError(f"candidate frame mismatch: {len(candidate)} != {args.expected_frames}")
    reference = reference[:args.expected_frames]
    sample_end = args.sample_end if args.sample_end is not None else args.expected_frames
    if not (0 <= args.sample_start < sample_end <= args.expected_frames):
        raise ValueError("invalid alignment sample range")

    shift_mse = {}
    for shift in range(-args.max_shift, args.max_shift + 1):
        errors = []
        for reference_index in range(args.sample_start, sample_end):
            candidate_index = reference_index + shift
            if not 0 <= candidate_index < len(candidate):
                continue
            before = reference[reference_index].astype(np.int16)
            after = candidate[candidate_index].astype(np.int16)
            errors.append(float(np.mean((before - after) ** 2)))
        shift_mse[str(shift)] = round(sum(errors) / len(errors), 6)
    best_shift = min(shift_mse, key=shift_mse.get)
    if int(best_shift) != 0:
        raise ValueError(f"candidate is frame-shifted: best shift is {best_shift}")

    report = {
        "schema_version": 1,
        "observation_id": "MOVIE-SUBTITLE-STR-ALIGNMENT-001",
        "reference": relative_path(reference_path, project),
        "reference_sha256": sha256_file(reference_path),
        "candidate": relative_path(candidate_path, project),
        "candidate_sha256": sha256_file(candidate_path),
        "expected_frames": args.expected_frames,
        "reference_frames": len(reference),
        "candidate_frames": len(candidate),
        "sample_frame_interval": [args.sample_start, sample_end],
        "candidate_index_equals_reference_index_plus_shift": shift_mse,
        "best_shift": int(best_shift),
        "validation": {
            "candidate_frame_count_matches": len(candidate) == args.expected_frames,
            "best_alignment_is_zero_frames": int(best_shift) == 0,
        },
        "status": "FRAME_ALIGNMENT_STATIC_PASS",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "best_shift": report["best_shift"],
        "zero_shift_mse": shift_mse["0"],
        "report": relative_path(report_path, project),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
