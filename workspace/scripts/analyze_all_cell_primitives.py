#!/usr/bin/env python3
"""Compare all 16x16 POLY_FT4 cell quads between title and Options RAM."""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

from capture_duckstation_ram import RAM_BASE, RAM_SIZE


def parse(data: bytes) -> list[dict[str, object]]:
    records = []
    for offset in range(0, len(data) - 40, 4):
        tag = struct.unpack_from("<I", data, offset)[0]
        code = data[offset + 7]
        if tag >> 24 != 9 or code not in (0x2C, 0x2E):
            continue
        xy = [struct.unpack_from("<hh", data, offset + base) for base in (8, 16, 24, 32)]
        uv = [(data[offset + base], data[offset + base + 1]) for base in (12, 20, 28, 36)]
        if any(not (-2048 <= x <= 4096 and -2048 <= y <= 4096) for x, y in xy):
            continue
        us = [u for u, _ in uv]
        vs = [v for _, v in uv]
        min_u, max_u, min_v, max_v = min(us), max(us), min(vs), max(vs)
        if max_u - min_u not in (15, 16) or max_v - min_v not in (15, 16):
            continue
        if set(us) != {min_u, max_u} or set(vs) != {min_v, max_v}:
            continue
        clut = struct.unpack_from("<H", data, offset + 14)[0]
        tpage = struct.unpack_from("<H", data, offset + 22)[0]
        records.append(
            {
                "address": RAM_BASE + offset,
                "offset": offset,
                "gpu_code": code,
                "xy": xy,
                "uv": uv,
                "u": min_u,
                "v": min_v,
                "clut": clut,
                "tpage": tpage,
                "cell_code_if_aligned": ((min_v // 16) * 16 + min_u // 16) if min_u % 16 == 0 and min_v % 16 == 0 else None,
            }
        )
    return records


def signature(record: dict[str, object]) -> tuple[object, ...]:
    return (record["gpu_code"], tuple(record["xy"]), tuple(record["uv"]), record["clut"], record["tpage"])


def make_groups(records: list[dict[str, object]]) -> list[dict[str, object]]:
    records = sorted(records, key=lambda item: int(item["offset"]))
    groups = []
    current = []
    for record in records:
        if current and int(record["offset"]) != int(current[-1]["offset"]) + 40:
            if len(current) >= 2:
                groups.append(current)
            current = []
        current.append(record)
    if len(current) >= 2:
        groups.append(current)
    return [
        {
            "address": f"0x{int(group[0]['address']):08X}",
            "count": len(group),
            "tpages": sorted({f"0x{int(item['tpage']):04X}" for item in group}),
            "cluts": sorted({f"0x{int(item['clut']):04X}" for item in group}),
            "cells": [f"{int(item['cell_code_if_aligned']):02X}" if item["cell_code_if_aligned"] is not None else None for item in group],
            "xy0": [item["xy"][0] for item in group],
            "uv_bounds": [[item["u"], item["v"]] for item in group],
        }
        for group in groups
    ]


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
        raise ValueError("expected complete 2MB RAM snapshots")
    title_records = parse(title)
    options_records = parse(options)
    title_at = {int(item["address"]): item for item in title_records}
    changed = [item for item in options_records if int(item["address"]) not in title_at or signature(item) != signature(title_at[int(item["address"])])]
    changed_addresses = {int(item["address"]) for item in changed}
    options_groups = make_groups(options_records)
    changed_groups = [
        group
        for group in options_groups
        if any(int(group["address"], 16) + index * 40 in changed_addresses for index in range(int(group["count"])))
    ]
    report = {
        "schema_version": 1,
        "observation_id": "CELL-PRIMITIVE-DIFF-001",
        "status": "CHANGED_GROUPS_FOUND" if changed_groups else "NO_CHANGED_GROUPS",
        "title_record_count": len(title_records),
        "options_record_count": len(options_records),
        "changed_record_count": len(changed),
        "options_group_count": len(options_groups),
        "changed_group_count": len(changed_groups),
        "changed_groups": changed_groups,
        "all_options_groups": options_groups,
        "interpretation_rule": "These are geometric 16x16 texture-cell quads. Font identity still requires matching tpage/clut or runtime asset evidence.",
    }
    output = project / "work/analysis" / f"{args.session_id}_all_cell_primitives.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "title_record_count", "options_record_count", "changed_record_count", "options_group_count", "changed_group_count")}, ensure_ascii=False))
    print(output)


if __name__ == "__main__":
    main()
