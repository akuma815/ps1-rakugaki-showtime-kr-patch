#!/usr/bin/env python3
"""Merge psxavenc video sectors into an original STR while preserving XA.

Only Mode 2 Form 1 video user data is replaced. Every non-video sector,
especially XA Form 2 audio and its final EOF sector, remains byte-identical.
The donor can contain leading alignment frames; selected donor frame numbers
are rewritten to the original frame numbers before EDC/ECC regeneration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import struct
import sys
from pathlib import Path

from extract_psx_movies import parse_str_header
from psx_disc import RAW_SECTOR_SIZE
from psx_edc_ecc import regenerate_mode2_form1, verify_mode2_form1


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


USER_DATA_START = 24
USER_DATA_SIZE = 2048
STR_CHUNK_DATA_SIZE = 2016


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_path(path: Path, project: Path) -> str:
    try:
        return path.relative_to(project).as_posix()
    except ValueError:
        return str(path)


def split_sectors(data: bytes) -> list[bytes]:
    if len(data) % RAW_SECTOR_SIZE:
        raise ValueError(f"raw STR size is not divisible by {RAW_SECTOR_SIZE}: {len(data)}")
    return [data[offset:offset + RAW_SECTOR_SIZE] for offset in range(0, len(data), RAW_SECTOR_SIZE)]


def collect_video_frames(sectors: list[bytes]) -> dict[int, dict[int, dict]]:
    frames: dict[int, dict[int, dict]] = {}
    for sector_index, raw in enumerate(sectors):
        payload = raw[USER_DATA_START:USER_DATA_START + USER_DATA_SIZE]
        header = parse_str_header(payload)
        if header is None:
            continue
        frame_number = header["frame_number"]
        chunk_index = header["chunk_index"]
        frame = frames.setdefault(frame_number, {})
        if chunk_index in frame:
            raise ValueError(f"duplicate frame/chunk {frame_number}/{chunk_index}")
        frame[chunk_index] = {
            "sector_index": sector_index,
            "payload": payload,
            **header,
        }
    for frame_number, chunks in frames.items():
        counts = {item["chunks_in_frame"] for item in chunks.values()}
        if len(counts) != 1:
            raise ValueError(f"inconsistent chunk counts in frame {frame_number}: {counts}")
        expected = next(iter(counts))
        if sorted(chunks) != list(range(expected)):
            raise ValueError(f"incomplete chunks in frame {frame_number}: {sorted(chunks)} != 0..{expected - 1}")
    return frames


def select_donor_frame_pairs(
    original: dict[int, dict[int, dict]],
    donor: dict[int, dict[int, dict]],
    drop_leading_frames: int,
) -> list[tuple[int, int]]:
    original_numbers = sorted(original)
    donor_numbers = sorted(donor)
    if original_numbers != list(range(original_numbers[0], original_numbers[0] + len(original_numbers))):
        raise ValueError("original frame numbers are not contiguous")
    if donor_numbers != list(range(donor_numbers[0], donor_numbers[0] + len(donor_numbers))):
        raise ValueError("donor frame numbers are not contiguous")
    selected = donor_numbers[drop_leading_frames:drop_leading_frames + len(original_numbers)]
    if len(selected) != len(original_numbers):
        raise ValueError(
            f"donor has too few frames after dropping {drop_leading_frames}: "
            f"{len(selected)} < {len(original_numbers)}"
        )
    pairs = list(zip(original_numbers, selected))
    for original_number, donor_number in pairs:
        original_chunks = len(original[original_number])
        donor_chunks = len(donor[donor_number])
        if donor_chunks != original_chunks:
            raise ValueError(
                f"frame chunk budget mismatch: original {original_number} has {original_chunks}, "
                f"donor {donor_number} has {donor_chunks}"
            )
    return pairs


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    try:
        with temporary.open("wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--donor", type=Path, required=True)
    parser.add_argument("--donor-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--drop-leading-frames", type=int, default=0)
    parser.add_argument("--label", default="STR_VIDEO_MERGE")
    args = parser.parse_args()

    project = args.project.resolve()
    original_path = args.original if args.original.is_absolute() else project / args.original
    donor_path = args.donor if args.donor.is_absolute() else project / args.donor
    donor_report_path = None
    donor_report = None
    if args.donor_report is not None:
        donor_report_path = args.donor_report if args.donor_report.is_absolute() else project / args.donor_report
        donor_report = json.loads(donor_report_path.read_text(encoding="utf-8"))
    output_path = args.output if args.output.is_absolute() else project / args.output
    report_path = args.report if args.report.is_absolute() else project / args.report
    if args.drop_leading_frames < 0:
        raise ValueError("drop-leading-frames cannot be negative")

    original_data = original_path.read_bytes()
    donor_data = donor_path.read_bytes()
    if donor_report is not None and donor_report["donor_str_sha256"] != sha256_bytes(donor_data):
        raise ValueError("donor STR hash differs from donor build report")
    original_sectors = split_sectors(original_data)
    donor_sectors = split_sectors(donor_data)
    original_frames = collect_video_frames(original_sectors)
    donor_frames = collect_video_frames(donor_sectors)
    pairs = select_donor_frame_pairs(original_frames, donor_frames, args.drop_leading_frames)

    replacements: dict[int, bytes] = {}
    donor_headroom = []
    frame_map = []
    for original_number, donor_number in pairs:
        original_chunks = original_frames[original_number]
        donor_chunks = donor_frames[donor_number]
        demuxed_sizes = {item["demuxed_size"] for item in donor_chunks.values()}
        if len(demuxed_sizes) != 1:
            raise ValueError(f"inconsistent donor demuxed size in frame {donor_number}")
        demuxed_size = next(iter(demuxed_sizes))
        capacity = len(donor_chunks) * STR_CHUNK_DATA_SIZE
        if demuxed_size > capacity:
            raise ValueError(f"donor frame {donor_number} exceeds its chunk capacity")
        donor_headroom.append(capacity - demuxed_size)
        frame_map.append({
            "original_frame_number": original_number,
            "donor_frame_number": donor_number,
            "chunks": len(donor_chunks),
            "demuxed_size": demuxed_size,
            "headroom": capacity - demuxed_size,
        })
        for chunk_index in sorted(original_chunks):
            original_item = original_chunks[chunk_index]
            donor_item = donor_chunks[chunk_index]
            payload = bytearray(donor_item["payload"])
            struct.pack_into("<I", payload, 8, original_number)
            replacements[original_item["sector_index"]] = bytes(payload)

    candidate_sectors = list(original_sectors)
    for sector_index, payload in replacements.items():
        sector = bytearray(original_sectors[sector_index])
        sector[USER_DATA_START:USER_DATA_START + USER_DATA_SIZE] = payload
        repaired = regenerate_mode2_form1(bytes(sector))
        if not verify_mode2_form1(repaired):
            raise ValueError(f"EDC/ECC regeneration failed at relative sector {sector_index}")
        candidate_sectors[sector_index] = repaired
    candidate_data = b"".join(candidate_sectors)

    candidate_frames = collect_video_frames(candidate_sectors)
    if sorted(candidate_frames) != sorted(original_frames):
        raise ValueError("candidate frame numbering differs from original")
    for frame_number in original_frames:
        if len(candidate_frames[frame_number]) != len(original_frames[frame_number]):
            raise ValueError(f"candidate chunk count differs at frame {frame_number}")

    original_video_indices = {
        item["sector_index"]
        for chunks in original_frames.values()
        for item in chunks.values()
    }
    if set(replacements) != original_video_indices:
        raise ValueError("not every original video sector was replaced exactly once")
    nonvideo_indices = set(range(len(original_sectors))) - original_video_indices
    changed_indices = [
        index for index, (before, after) in enumerate(zip(original_sectors, candidate_sectors))
        if before != after
    ]
    if any(candidate_sectors[index] != original_sectors[index] for index in nonvideo_indices):
        raise ValueError("a non-video sector changed")
    audio_indices = [
        index
        for index in nonvideo_indices
        if original_sectors[index][18] & 0x04
    ]
    original_audio = b"".join(original_sectors[index] for index in audio_indices)
    candidate_audio = b"".join(candidate_sectors[index] for index in audio_indices)
    if candidate_audio != original_audio:
        raise ValueError("XA audio sectors changed")

    atomic_write(output_path, candidate_data)
    report = {
        "schema_version": 1,
        "observation_id": args.label,
        "source_original": relative_path(original_path, project),
        "source_original_sha256": sha256_bytes(original_data),
        "source_donor": relative_path(donor_path, project),
        "source_donor_sha256": sha256_bytes(donor_data),
        "source_donor_report": relative_path(donor_report_path, project) if donor_report_path else None,
        "source_donor_report_sha256": sha256_file(donor_report_path) if donor_report_path else None,
        "output": relative_path(output_path, project),
        "output_size": len(candidate_data),
        "output_sha256": sha256_bytes(candidate_data),
        "drop_leading_donor_frames": args.drop_leading_frames,
        "original_sector_count": len(original_sectors),
        "donor_sector_count": len(donor_sectors),
        "frame_count": len(pairs),
        "video_sector_count": len(original_video_indices),
        "changed_video_sector_count": len(changed_indices),
        "changed_nonvideo_sector_count": 0,
        "xa_sector_count": len(audio_indices),
        "xa_sha256_before": sha256_bytes(original_audio),
        "xa_sha256_after": sha256_bytes(candidate_audio),
        "xa_byte_identical": candidate_audio == original_audio,
        "donor_frame_headroom": {
            "minimum": min(donor_headroom),
            "median": statistics.median(donor_headroom),
            "maximum": max(donor_headroom),
        },
        "frame_map": frame_map,
        "validation": {
            "output_size_preserved": len(candidate_data) == len(original_data),
            "all_original_video_slots_replaced": set(replacements) == original_video_indices,
            "all_nonvideo_sectors_byte_identical": True,
            "all_changed_video_sectors_edc_ecc_valid": all(
                verify_mode2_form1(candidate_sectors[index]) for index in changed_indices
            ),
            "frame_numbers_and_chunk_budgets_preserved": True,
        },
        "status": "STR_VIDEO_MERGE_STATIC_PASS_RUNTIME_NOT_BUILT",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "frames": report["frame_count"],
        "video_sectors": report["video_sector_count"],
        "xa_byte_identical": report["xa_byte_identical"],
        "output": report["output"],
        "report": relative_path(report_path, project),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
