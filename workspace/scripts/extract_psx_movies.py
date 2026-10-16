#!/usr/bin/env python3
"""Catalog and extract Rakugaki Showtime's raw PlayStation STR extents.

ISO 9660 records for CD-XA streams express their allocation in 2048-byte
logical blocks even when individual sectors are Mode 2 Form 2.  A generic
payload concatenator therefore stops early on these files.  This tool copies
the declared number of complete 2352-byte sectors and records the XA/STR
structure needed by the subtitle-reinsertion PoC.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import struct
import sys
from collections import Counter
from pathlib import Path

from psx_disc import RAW_SECTOR_SIZE, read_raw_sector, sector_payload


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


STR_MAGIC = 0x80010160
ISO_LOGICAL_BLOCK_SIZE = 2048


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iso_extent_sector_count(size: int) -> int:
    if size < 0:
        raise ValueError("ISO extent size cannot be negative")
    return math.ceil(size / ISO_LOGICAL_BLOCK_SIZE)


def parse_str_header(payload: bytes) -> dict | None:
    if len(payload) < 20:
        return None
    magic, chunk_index, chunks_in_frame, frame_number, demuxed_size, width, height = struct.unpack_from(
        "<IHHIIHH", payload, 0
    )
    if magic != STR_MAGIC:
        return None
    if not chunks_in_frame or chunk_index >= chunks_in_frame:
        raise ValueError(
            f"invalid STR chunk header: index={chunk_index}, count={chunks_in_frame}, frame={frame_number}"
        )
    return {
        "chunk_index": chunk_index,
        "chunks_in_frame": chunks_in_frame,
        "frame_number": frame_number,
        "demuxed_size": demuxed_size,
        "width": width,
        "height": height,
    }


def decode_xa_audio_coding(coding: int) -> dict:
    if coding & 0xCA:
        raise ValueError(f"unsupported/reserved XA audio coding bits: 0x{coding:02X}")
    channels = 2 if coding & 0x01 else 1
    sample_rate = 18900 if coding & 0x04 else 37800
    bits_per_sample = 8 if coding & 0x10 else 4
    samples_per_channel = 4032 // channels // (2 if bits_per_sample == 8 else 1)
    return {
        "coding_hex": f"{coding:02x}",
        "channels": channels,
        "sample_rate": sample_rate,
        "bits_per_sample": bits_per_sample,
        "samples_per_sector_per_channel": samples_per_channel,
        "seconds_per_sector": samples_per_channel / sample_rate,
    }


def format_counter(counter: Counter[tuple[int, ...]]) -> list[dict]:
    return [
        {"bytes_hex": bytes(key).hex(), "count": count}
        for key, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


def inspect_and_extract(track, entry: dict, destination: Path | None) -> dict:
    sector_count = iso_extent_sector_count(entry["size"])
    raw_digest = hashlib.sha256()
    subheaders: Counter[tuple[int, ...]] = Counter()
    forms: Counter[tuple[int, ...]] = Counter()
    video_frames: set[int] = set()
    video_chunks = Counter()
    video_dimensions = Counter()
    video_demuxed_sizes: list[int] = []
    video_sector_count = 0
    audio_sector_count = 0
    audio_codings: Counter[int] = Counter()
    other_sector_count = 0

    temporary: Path | None = None
    output = None
    try:
        if destination is not None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            if temporary.exists():
                temporary.unlink()
            output = temporary.open("wb")

        for relative_sector in range(sector_count):
            lba = entry["lba"] + relative_sector
            raw = read_raw_sector(track, lba)
            raw_digest.update(raw)
            if output is not None:
                output.write(raw)

            subheader = tuple(raw[16:20])
            subheaders[subheader] += 1
            payload, form = sector_payload(raw)
            forms[(1 if form == "form1" else 2,)] += 1
            header = parse_str_header(payload)
            if header is not None:
                video_sector_count += 1
                video_frames.add(header["frame_number"])
                video_chunks[header["chunks_in_frame"]] += 1
                video_dimensions[(header["width"], header["height"])] += 1
                if header["chunk_index"] == 0:
                    video_demuxed_sizes.append(header["demuxed_size"])
            elif raw[18] & 0x04:
                audio_sector_count += 1
                audio_codings[raw[19]] += 1
            else:
                other_sector_count += 1

        if output is not None:
            output.flush()
            os.fsync(output.fileno())
            output.close()
            output = None
            if temporary is None or destination is None:
                raise AssertionError("temporary output state lost")
            temporary.replace(destination)
    finally:
        if output is not None:
            output.close()
        if temporary is not None and temporary.exists():
            temporary.unlink()

    audio_profiles = []
    duration_seconds = 0.0
    for coding, count in sorted(audio_codings.items()):
        profile = decode_xa_audio_coding(coding)
        duration = count * profile["seconds_per_sector"]
        audio_profiles.append({
            **profile,
            "sector_count": count,
            "duration_seconds": round(duration, 6),
        })
        duration_seconds += duration
    frame_count = len(video_frames)
    return {
        "asset_id": f"MOVIE-{Path(entry['path']).stem}",
        "disc_path": entry["path"],
        "iso_lba": entry["lba"],
        "iso_size": entry["size"],
        "sector_count": sector_count,
        "last_lba": entry["lba"] + sector_count - 1,
        "raw_size": sector_count * RAW_SECTOR_SIZE,
        "raw_sha256": raw_digest.hexdigest(),
        "output": destination.as_posix() if destination is not None else None,
        "duration_seconds": round(duration_seconds, 6),
        "physical_sector_rate": round(sector_count / duration_seconds, 6) if duration_seconds else None,
        "subheaders": format_counter(subheaders),
        "sector_forms": {
            "form1": forms[(1,)],
            "form2": forms[(2,)],
        },
        "video": {
            "sector_count": video_sector_count,
            "frame_count": frame_count,
            "frame_number_min": min(video_frames) if video_frames else None,
            "frame_number_max": max(video_frames) if video_frames else None,
            "estimated_fps": round(frame_count / duration_seconds, 6) if duration_seconds else None,
            "chunks_per_frame": [
                {"chunks": chunks, "sector_count": count}
                for chunks, count in sorted(video_chunks.items())
            ],
            "dimensions": [
                {"width": dims[0], "height": dims[1], "sector_count": count}
                for dims, count in sorted(video_dimensions.items())
            ],
            "demuxed_size_min": min(video_demuxed_sizes) if video_demuxed_sizes else None,
            "demuxed_size_max": max(video_demuxed_sizes) if video_demuxed_sizes else None,
        },
        "audio": {
            "sector_count": audio_sector_count,
            "profiles": audio_profiles,
        },
        "other_sector_count": other_sector_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--catalog-only", action="store_true", help="inspect sectors without writing raw STR files")
    args = parser.parse_args()

    project = args.project.resolve()
    source_manifest = json.loads((project / "workspace/config/source_manifest.json").read_text(encoding="utf-8"))
    disc_manifest = json.loads((project / "work/analysis/disc_manifest.json").read_text(encoding="utf-8"))
    image = project / source_manifest["disc"]["bin"]
    expected_hash = source_manifest["disc"]["bin_sha256"]
    actual_hash = sha256_file(image)
    if actual_hash != expected_hash:
        raise ValueError(f"source BIN hash mismatch: {actual_hash} != {expected_hash}")

    movie_entries = sorted(
        (item for item in disc_manifest["entries"] if item.get("path", "").upper().endswith(".STR")),
        key=lambda item: item["lba"],
    )
    if not movie_entries:
        raise ValueError("no STR entries found in disc manifest")

    records = []
    with image.open("rb") as track:
        for entry in movie_entries:
            destination = None if args.catalog_only else project / "extracted" / Path(entry["path"])
            record = inspect_and_extract(track, entry, destination)
            if destination is not None:
                record["output"] = destination.relative_to(project).as_posix()
            if record["last_lba"] >= source_manifest["disc"]["sector_count"]:
                raise ValueError(f"movie extent leaves source track: {record['disc_path']}")
            records.append(record)

    for current, following in zip(records, records[1:]):
        if current["last_lba"] >= following["iso_lba"]:
            raise ValueError(f"overlapping movie extents: {current['disc_path']} and {following['disc_path']}")

    report = {
        "schema_version": 1,
        "observation_id": "PSX-MOVIE-CATALOG-001",
        "source": {
            "bin": source_manifest["disc"]["bin"],
            "bin_sha256": actual_hash,
            "sector_size": RAW_SECTOR_SIZE,
            "iso_logical_block_size": ISO_LOGICAL_BLOCK_SIZE,
        },
        "extraction_rule": "raw sector count = ceil(ISO directory size / 2048); copy complete 2352-byte sectors",
        "movies": records,
        "status": "RAW_STR_EXTRACTED" if not args.catalog_only else "CATALOG_ONLY",
    }
    report_name = "movie_catalog_catalog_only.json" if args.catalog_only else "movie_catalog_001.json"
    report_path = project / "work/analysis" / report_name
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "movies": len(records),
        "raw_bytes": sum(item["raw_size"] for item in records),
        "report": report_path.relative_to(project).as_posix(),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
