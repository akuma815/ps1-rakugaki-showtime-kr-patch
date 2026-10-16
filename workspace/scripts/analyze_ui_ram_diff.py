#!/usr/bin/env python3
"""Analyze title/options RAM snapshots for candidate font codes and UVs."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

from capture_duckstation_ram import RAM_BASE, RAM_SIZE, find_all
from scan_font_strings import encode


ANCHORS = [
    "オプション",
    "セレクト",
    "ユーザーフレンドリー",
    "サウンド",
    "デモンストレーション",
    "スタッフ",
    "データ",
    "キーコンフィグ",
    "モード",
]


def variants(codes: bytes) -> dict[str, bytes]:
    return {
        "code_u8": codes,
        "code_u16le": b"".join(struct.pack("<H", value) for value in codes),
        "code_u32le": b"".join(struct.pack("<I", value) for value in codes),
        "uv_u8": b"".join(bytes(((value & 0x0F) * 16, (value >> 4) * 16)) for value in codes),
        "uv_u16le": b"".join(struct.pack("<HH", (value & 0x0F) * 16, (value >> 4) * 16) for value in codes),
    }


def addresses(data: bytes, needle: bytes, limit: int = 64) -> list[str]:
    return [f"0x{RAM_BASE + offset:08X}" for offset in find_all(data, needle)[:limit]]


def changed_ranges(left: bytes, right: bytes) -> list[tuple[int, int]]:
    ranges = []
    start = None
    for index, (a, b) in enumerate(zip(left, right)):
        if a != b and start is None:
            start = index
        elif a == b and start is not None:
            ranges.append((start, index))
            start = None
    if start is not None:
        ranges.append((start, min(len(left), len(right))))
    return ranges


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--session-id", default="ui_ram_session_001")
    args = parser.parse_args()

    project = args.project.resolve()
    root = project / "work/runtime" / args.session_id
    title = (root / "ram_title.bin").read_bytes()
    options = (root / "ram_options.bin").read_bytes()
    if len(title) != RAM_SIZE or len(options) != RAM_SIZE:
        raise ValueError("expected two complete 2MB RAM snapshots")

    anchor_records = []
    for text in ANCHORS:
        codes = encode(text)
        record = {"text": text, "codes_hex": codes.hex(" ").upper(), "variants": []}
        for name, needle in variants(codes).items():
            title_hits = addresses(title, needle)
            options_hits = addresses(options, needle)
            record["variants"].append(
                {
                    "encoding": name,
                    "needle_hex": needle.hex(" ").upper(),
                    "title_hit_count": len(find_all(title, needle)),
                    "options_hit_count": len(find_all(options, needle)),
                    "title_addresses": title_hits,
                    "options_addresses": options_hits,
                    "options_only_addresses": sorted(set(options_hits) - set(title_hits)),
                }
            )

        prefixes = []
        for length in range(len(codes) - 1, 1, -1):
            needle = codes[:length]
            title_hits = find_all(title, needle)
            options_hits = find_all(options, needle)
            if title_hits or options_hits:
                prefixes.append(
                    {
                        "length": length,
                        "needle_hex": needle.hex(" ").upper(),
                        "title_hit_count": len(title_hits),
                        "options_hit_count": len(options_hits),
                        "options_only_addresses": [
                            f"0x{RAM_BASE + offset:08X}" for offset in sorted(set(options_hits) - set(title_hits))[:64]
                        ],
                    }
                )
                break
        record["longest_prefix_with_hit"] = prefixes[0] if prefixes else None
        anchor_records.append(record)

    ranges = changed_ranges(title, options)
    differing_bytes = sum(end - start for start, end in ranges)
    largest = sorted(ranges, key=lambda item: item[1] - item[0], reverse=True)[:64]
    report = {
        "schema_version": 1,
        "observation_id": "FONT-CODE-001",
        "status": "ANALYZED",
        "session_id": args.session_id,
        "ram": {
            "base": f"0x{RAM_BASE:08X}",
            "size": RAM_SIZE,
            "title_sha256": hashlib.sha256(title).hexdigest(),
            "options_sha256": hashlib.sha256(options).hexdigest(),
            "differing_byte_count": differing_bytes,
            "contiguous_changed_range_count": len(ranges),
            "largest_changed_ranges": [
                {
                    "address": f"0x{RAM_BASE + start:08X}",
                    "size": end - start,
                    "title_hex": title[start : min(end, start + 64)].hex(" ").upper(),
                    "options_hex": options[start : min(end, start + 64)].hex(" ").upper(),
                }
                for start, end in largest
            ],
        },
        "anchors": anchor_records,
        "interpretation_rule": "Exact runtime hits support a representation candidate only; they do not prove the on-disc storage format or the consumer without a read/primitive trace.",
    }
    output = project / "work/analysis" / f"{args.session_id}_diff.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = []
    for record in anchor_records:
        hit_variants = [item for item in record["variants"] if item["options_hit_count"]]
        summary.append({"text": record["text"], "hits": [(item["encoding"], item["options_hit_count"]) for item in hit_variants], "prefix": record["longest_prefix_with_hit"]})
    print(json.dumps({"differing_bytes": differing_bytes, "changed_ranges": len(ranges), "anchors": summary}, ensure_ascii=False, indent=2))
    print(output)


if __name__ == "__main__":
    main()
