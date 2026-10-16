#!/usr/bin/env python3
"""Find PS1 textured-sprite structures that select primary-font cells."""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

from capture_duckstation_ram import RAM_BASE, RAM_SIZE
from scan_font_strings import KATAKANA


def reverse_mapping() -> dict[int, str]:
    result = {value: char for char, value in KATAKANA.items()}
    for char in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz":
        result[ord(char) - 0x30] = char
    return result


def parse_candidates(data: bytes, base_address: int = RAM_BASE) -> list[dict[str, object]]:
    candidates = []
    cluts = {((288 // 16) | ((496 + row) << 6)): row for row in range(8)}
    for offset in range(0, len(data) - 20, 4):
        tag = struct.unpack_from("<I", data, offset)[0]
        packet_words = tag >> 24
        code = data[offset + 7]
        if code in (0x64, 0x66, 0x74, 0x76, 0x7C, 0x7E):
            expected_words = 4 if code in (0x64, 0x66) else 3
            if packet_words != expected_words:
                continue
            x, y = struct.unpack_from("<hh", data, offset + 8)
            u, v = data[offset + 12], data[offset + 13]
            clut = struct.unpack_from("<H", data, offset + 14)[0]
            if clut not in cluts or u % 16 or v % 16:
                continue
            if not (-1024 <= x <= 2048 and -1024 <= y <= 2048):
                continue
            glyph_code = (v // 16) * 16 + (u // 16)
            record = {
                "structure": "SPRT",
                "size": 20 if code in (0x64, 0x66) else 16,
                "address": base_address + offset,
                "offset": offset,
                "packet_words": packet_words,
                "gpu_code": code,
                "x": x,
                "y": y,
                "u": u,
                "v": v,
                "clut": clut,
                "palette_row": cluts[clut],
                "glyph_code": glyph_code,
            }
            if code in (0x64, 0x66):
                record["width"], record["height"] = struct.unpack_from("<hh", data, offset + 16)
            candidates.append(record)
            continue

        if code not in (0x2C, 0x2E) or packet_words != 9:
            continue
        clut = struct.unpack_from("<H", data, offset + 14)[0]
        tpage = struct.unpack_from("<H", data, offset + 22)[0]
        if clut not in cluts or (tpage & 0x1F) != 0x1F:
            continue
        xy = [struct.unpack_from("<hh", data, offset + base) for base in (8, 16, 24, 32)]
        uv = [(data[offset + base], data[offset + base + 1]) for base in (12, 20, 28, 36)]
        if any(not (-1024 <= x <= 2048 and -1024 <= y <= 2048) for x, y in xy):
            continue
        us = [u for u, _ in uv]
        vs = [v for _, v in uv]
        min_u, max_u, min_v, max_v = min(us), max(us), min(vs), max(vs)
        if min_u % 16 or min_v % 16 or max_u - min_u not in (15, 16) or max_v - min_v not in (15, 16):
            continue
        if set(us) != {min_u, max_u} or set(vs) != {min_v, max_v}:
            continue
        glyph_code = (min_v // 16) * 16 + (min_u // 16)
        candidates.append(
            {
                "structure": "POLY_FT4",
                "size": 40,
                "address": base_address + offset,
                "offset": offset,
                "packet_words": packet_words,
                "gpu_code": code,
                "x": xy[0][0],
                "y": xy[0][1],
                "screen_xy": xy,
                "u": min_u,
                "v": min_v,
                "uv": uv,
                "clut": clut,
                "tpage": tpage,
                "palette_row": cluts[clut],
                "glyph_code": glyph_code,
            }
        )
    return candidates


def groups(candidates: list[dict[str, object]], mapping: dict[int, str]) -> list[dict[str, object]]:
    fixed = [item for item in candidates if item["structure"] in ("POLY_FT4", "SPRT") and int(item["size"]) in (16, 40)]
    fixed.sort(key=lambda item: int(item["offset"]))
    output = []
    current = []
    for item in fixed:
        if current and (
            item["structure"] != current[-1]["structure"]
            or int(item["offset"]) != int(current[-1]["offset"]) + int(current[-1]["size"])
        ):
            if len(current) >= 2:
                output.append(current)
            current = []
        current.append(item)
    if len(current) >= 2:
        output.append(current)
    records = []
    for group in output:
        codes = [int(item["glyph_code"]) for item in group]
        records.append(
            {
                "address": f"0x{int(group[0]['address']):08X}",
                "count": len(group),
                "structure": group[0]["structure"],
                "stride": group[0]["size"],
                "codes_hex": " ".join(f"{value:02X}" for value in codes),
                "decoded": "".join(mapping.get(value, f"<{value:02X}>") for value in codes),
                "screen_xy": [[item["x"], item["y"]] for item in group],
                "palette_rows": [item["palette_row"] for item in group],
            }
        )
    return records


def signature(item: dict[str, object]) -> tuple[object, ...]:
    return tuple(item[key] for key in ("structure", "gpu_code", "x", "y", "u", "v", "clut", "glyph_code"))


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
    mapping = reverse_mapping()
    title_candidates = parse_candidates(title)
    options_candidates = parse_candidates(options)
    title_by_address = {int(item["address"]): item for item in title_candidates}
    changed_options = [
        item
        for item in options_candidates
        if int(item["address"]) not in title_by_address or signature(item) != signature(title_by_address[int(item["address"])])
    ]
    option_groups = groups(options_candidates, mapping)
    changed_addresses = {int(item["address"]) for item in changed_options}
    changed_groups = [
        group
        for group in option_groups
        if any(int(group["address"], 16) + index * int(group["stride"]) in changed_addresses for index in range(int(group["count"])))
    ]
    report = {
        "schema_version": 1,
        "observation_id": "FONT-PRIMITIVE-001",
        "status": "CANDIDATES_FOUND" if changed_options else "NO_CANDIDATES",
        "layout_hypothesis": "PsyQ-style POLY_FT4 or SPRT: four-byte tag followed by RGB/code, XY, UV, CLUT and texture-page fields",
        "font_clut_rows": [f"0x{((288 // 16) | ((496 + row) << 6)):04X}" for row in range(8)],
        "title_candidate_count": len(title_candidates),
        "options_candidate_count": len(options_candidates),
        "changed_options_candidate_count": len(changed_options),
        "changed_options_candidates": [
            {**item, "address": f"0x{int(item['address']):08X}", "offset": f"0x{int(item['offset']):X}", "gpu_code": f"0x{int(item['gpu_code']):02X}", "clut": f"0x{int(item['clut']):04X}", "tpage": f"0x{int(item['tpage']):04X}" if "tpage" in item else None, "glyph_code": f"0x{int(item['glyph_code']):02X}"}
            for item in changed_options[:2048]
        ],
        "options_contiguous_groups": option_groups,
        "changed_options_groups": changed_groups,
        "interpretation_rule": "A structure match is a primitive candidate until its address is linked to the active ordering table or a controlled glyph change affects the corresponding screen character.",
    }
    output = project / "work/analysis" / f"{args.session_id}_font_primitives.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "title": len(title_candidates), "options": len(options_candidates), "changed": len(changed_options), "groups": changed_groups[:20]}, ensure_ascii=False, indent=2))
    print(output)


if __name__ == "__main__":
    main()
