#!/usr/bin/env python3
"""Inventory decoder codes, string candidates, and confirmed/candidate consumers."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

OVERLAY_BASE = 0x8012BCE0
GENERIC_STRING_BUILDER = 0x80043348
TITLE_STRING_BUILDER = 0x80144CFC
TITLE_PROMPT_OFFSET = 0x1A064
TITLE_PROMPT_RAW = bytes.fromhex("83 76 83 62 83 56 83 85 83 58 83 5E 81 5B 83 67 83 7B 83 5E 83 93")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def jal_word(address: int) -> bytes:
    return struct.pack("<I", 0x0C000000 | ((address >> 2) & 0x03FFFFFF))


def scan_calls(data: bytes, target: int) -> list[int]:
    pattern = jal_word(target)
    return [offset for offset in range(0, len(data) - 3, 4) if data[offset : offset + 4] == pattern]


def scan_candidate_runs(data: bytes, mapping: dict[int, dict], minimum_characters: int = 2) -> list[dict]:
    """Find maximal parity-aligned valid-code runs that end at a zero lead byte."""
    results: list[dict] = []
    for parity in (0, 1):
        cursor = parity
        while cursor + 1 < len(data):
            code = int.from_bytes(data[cursor : cursor + 2], "big")
            if code not in mapping:
                cursor += 2
                continue
            start = cursor
            codes: list[int] = []
            while cursor + 1 < len(data):
                code = int.from_bytes(data[cursor : cursor + 2], "big")
                if code not in mapping:
                    break
                codes.append(code)
                cursor += 2
            terminated = cursor < len(data) and data[cursor] == 0
            if terminated and len(codes) >= minimum_characters:
                raw = data[start:cursor]
                text = "".join(mapping[code]["character"] for code in codes)
                results.append({
                    "offset": start,
                    "byte_length": len(raw),
                    "character_count": len(codes),
                    "raw_hex": raw.hex().upper(),
                    "codes": [f"{code:04X}" for code in codes],
                    "cells": [mapping[code]["cell"] for code in codes],
                    "text": text,
                    "terminator": "lead-byte 00",
                })
            if cursor == start:
                cursor += 2
    # The same byte range cannot be a valid run in both parities, but de-duplicate defensively.
    return list({(item["offset"], item["byte_length"]): item for item in results}.values())


def decode_exact(raw: bytes, mapping: dict[int, dict]) -> tuple[str, list[int]]:
    if len(raw) % 2:
        raise ValueError("two-byte string has odd length")
    codes = [int.from_bytes(raw[offset : offset + 2], "big") for offset in range(0, len(raw), 2)]
    if any(code not in mapping for code in codes):
        raise ValueError("string contains an unmapped code")
    return "".join(mapping[code]["character"] for code in codes), codes


def signed16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def payload_offset(block: bytes) -> int:
    """Return the explicit payload offset used by wrapped HAGECHO modules."""
    if block[:8] == bytes.fromhex("00 07 00 00 10 00 00 00"):
        declared_size = struct.unpack_from("<I", block, 12)[0]
        if declared_size > len(block) - 0x10:
            raise ValueError("wrapped HAGECHO payload exceeds its block")
        return 0x10
    return 0


def overlay_pointer_candidates(block: bytes, block_index: int, block_start: int, module_payload_offset: int) -> list[dict]:
    """Find adjacent LUI + ADDIU/ORI address materializations into the current overlay."""
    pointers: list[dict] = []
    payload = block[module_payload_offset:]
    words = [struct.unpack_from("<I", payload, offset)[0] for offset in range(0, len(payload) - 3, 4)]
    for index, first in enumerate(words):
        if first >> 26 != 0x0F:  # LUI
            continue
        base_reg = (first >> 16) & 0x1F
        high = first & 0xFFFF
        for distance in range(1, min(5, len(words) - index)):
            second = words[index + distance]
            opcode = second >> 26
            source_reg = (second >> 21) & 0x1F
            if source_reg != base_reg or opcode not in (0x09, 0x0D):  # ADDIU / ORI
                continue
            low = second & 0xFFFF
            runtime = (high << 16) + (signed16(low) if opcode == 0x09 else low)
            payload_local = runtime - OVERLAY_BASE
            block_local = module_payload_offset + payload_local
            producer_block_local = module_payload_offset + index * 4
            if 0 <= block_local < len(block):
                pointers.append({
                    "block_index": block_index,
                    "module_payload_offset": module_payload_offset,
                    "producer_payload_offset": index * 4,
                    "producer_block_offset": producer_block_local,
                    "producer_file_offset": block_start + producer_block_local,
                    "instruction_distance": distance,
                    "runtime_target": f"0x{runtime:08X}",
                    "target_payload_offset": payload_local,
                    "target_block_offset": block_local,
                    "target_file_offset": block_start + block_local,
                })
            break
    return pointers


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    project = args.project.resolve()

    encoding = json.loads((project / "workspace/config/font_encoding.json").read_text(encoding="utf-8"))
    mapping = {int(item["sjis"], 16): item for item in encoding["mappings"]}
    hagecho = (project / "extracted/HAGECHO.BIN").read_bytes()
    ara = (project / "extracted/ARA.BIN").read_bytes()
    blocks = json.loads((project / "work/analysis/hagecho_blocks.json").read_text(encoding="utf-8"))["blocks"]

    title_text, title_codes = decode_exact(TITLE_PROMPT_RAW, mapping)
    if hagecho[TITLE_PROMPT_OFFSET : TITLE_PROMPT_OFFSET + len(TITLE_PROMPT_RAW)] != TITLE_PROMPT_RAW:
        raise ValueError("confirmed title prompt baseline mismatch")

    all_candidates: list[dict] = []
    all_pointers: list[dict] = []
    module_layouts: list[dict] = []
    hagecho_calls = scan_calls(hagecho, GENERIC_STRING_BUILDER)
    call_by_block: dict[int, list[dict]] = defaultdict(list)
    for block in blocks:
        start, end = block["start"], block["end"]
        block_data = hagecho[start:end]
        module_payload_offset = payload_offset(block_data)
        module_layouts.append({
            "block_index": block["index"],
            "file_start": start,
            "size": len(block_data),
            "payload_offset": module_payload_offset,
            "runtime_base": f"0x{OVERLAY_BASE:08X}",
            "mapping": "file_offset = block_start + payload_offset + (runtime_address - runtime_base)",
        })
        candidates = scan_candidate_runs(block_data, mapping)
        for item in candidates:
            item.update({
                "candidate_id": f"hagecho/b{block['index']:03d}/{item['offset']:06X}",
                "source": "HAGECHO.BIN",
                "block_index": block["index"],
                "block_offset": item["offset"],
                "file_offset": start + item["offset"],
                "status": "candidate_not_consumer_confirmed",
            })
        all_candidates.extend(candidates)

        pointers = overlay_pointer_candidates(block_data, block["index"], start, module_payload_offset)
        all_pointers.extend(pointers)
        for call_offset in (offset for offset in hagecho_calls if start <= offset < end):
            call_by_block[block["index"]].append({
                "file_offset": call_offset,
                "block_offset": call_offset - start,
                "payload_offset": call_offset - start - module_payload_offset,
                "runtime_address": f"0x{OVERLAY_BASE + call_offset - start - module_payload_offset:08X}",
            })

    ara_candidates = scan_candidate_runs(ara, mapping)
    for item in ara_candidates:
        item.update({
            "candidate_id": f"ara/{item['offset']:06X}",
            "source": "ARA.BIN",
            "file_offset": item["offset"],
            "status": "candidate_not_consumer_confirmed",
        })
    all_candidates.extend(ara_candidates)

    candidates_by_target = {(item["source"], item["file_offset"]): item for item in all_candidates}
    linked_pointer_count = 0
    for pointer in all_pointers:
        candidate = candidates_by_target.get(("HAGECHO.BIN", pointer["target_file_offset"]))
        if candidate is None:
            continue
        pointer["candidate_id"] = candidate["candidate_id"]
        candidate.setdefault("static_pointer_candidates", []).append({
            "producer_file_offset": pointer["producer_file_offset"],
            "runtime_target": pointer["runtime_target"],
        })
        candidate["status"] = "candidate_with_static_overlay_pointer"
        linked_pointer_count += 1

    title_id = "hagecho/b000/01A064"
    title_candidate = next(
        (item for item in all_candidates if item["source"] == "HAGECHO.BIN" and item["file_offset"] == TITLE_PROMPT_OFFSET),
        None,
    )
    if title_candidate is None:
        raise ValueError("confirmed title prompt was not recovered by candidate scanner")
    title_candidate["candidate_id"] = title_id
    title_candidate["status"] = "consumer_confirmed"
    title_candidate["consumer"] = {
        "reader_runtime": "0x80144D44",
        "string_builder_runtime": f"0x{TITLE_STRING_BUILDER:08X}",
        "runtime_pointer": "0x80145D44",
        "evidence": "work/analysis/font_writer_analysis.json",
    }

    candidate_code_counts: Counter[int] = Counter()
    confirmed_code_counts: Counter[int] = Counter(title_codes)
    for item in all_candidates:
        candidate_code_counts.update(int(code, 16) for code in item["codes"])

    unique_cells = {item["cell"] for item in encoding["mappings"]}
    occupied_atlas_cells = set(range(0xFF))
    decoder_unreachable = {int(value, 16) for value in encoding["missing_cells"]}
    decoder_reachable = occupied_atlas_cells - decoder_unreachable
    if unique_cells != decoder_reachable:
        raise ValueError("decoder mapping cell coverage changed")

    report = {
        "schema_version": 1,
        "observation_id": "TEXT-POPULATION-001",
        "status": "CONFIRMED_SUBSET_WITH_OPEN_POPULATION",
        "scope": {
            "revision_bin_sha256": "99dc6c7f748c4882d6f197a0a0a2eb943affc25bb4271deff9bb9bf55859aa54",
            "files": {
                "HAGECHO.BIN": {"size": len(hagecho), "sha256": sha256(hagecho)},
                "ARA.BIN": {"size": len(ara), "sha256": sha256(ara)},
            },
            "candidate_rule": "maximal parity-aligned runs of at least two decoder-valid two-byte codes followed by a zero lead byte",
            "candidate_limit": "Candidates are discovery evidence, not the confirmed translation population; one-character strings are not enumerated by this pass.",
        },
        "decoder_capacity": {
            "input_code_count": len(mapping),
            "unique_reachable_cells": len(unique_cells),
            "occupied_atlas_cells": len(occupied_atlas_cells),
            "unreachable_occupied_cells": [f"{cell:02X}" for cell in sorted(decoder_unreachable)],
            "invalid_cell": "FF",
            "alias_cells": encoding["duplicate_cells"],
            "currently_proven_free_reachable_cells": 0,
            "allocation_decision": "NO_SAFE_HANGUL_CODE_ALLOCATION_YET",
            "reason": "Every decoder-reachable cell is occupied; zero occurrence or decoder rejection alone does not prove a code/cell is reusable.",
        },
        "consumers": {
            "generic_string_builder_runtime": f"0x{GENERIC_STRING_BUILDER:08X}",
            "generic_builder_calls": {
                "ARA.BIN": [{"file_offset": offset} for offset in scan_calls(ara, GENERIC_STRING_BUILDER)],
                "HAGECHO.BIN": [
                    {"block_index": block_index, **record}
                    for block_index in sorted(call_by_block)
                    for record in call_by_block[block_index]
                ],
            },
            "confirmed_entries": [title_id],
            "confirmed_entry_count": 1,
            "generic_callsite_count": len(scan_calls(ara, GENERIC_STRING_BUILDER)) + len(hagecho_calls),
            "classification_scope": "This discovery report enumerates calls but delegates source resolution to per-module callsite reports.",
        },
        "module_layouts": module_layouts,
        "candidate_summary": {
            "total": len(all_candidates),
            "hagecho": len(all_candidates) - len(ara_candidates),
            "ara": len(ara_candidates),
            "with_static_overlay_pointer": sum(item["status"] == "candidate_with_static_overlay_pointer" for item in all_candidates),
            "consumer_confirmed": 1,
            "static_pointer_links": linked_pointer_count,
            "unique_candidate_codes": len(candidate_code_counts),
            "unique_confirmed_codes": len(confirmed_code_counts),
        },
        "candidate_code_usage": [
            {
                "sjis": f"{code:04X}",
                "character": mapping[code]["character"],
                "cell": mapping[code]["cell"],
                "candidate_occurrences": candidate_code_counts[code],
                "confirmed_occurrences": confirmed_code_counts[code],
            }
            for code in sorted(mapping)
        ],
        "candidates": sorted(all_candidates, key=lambda item: (item["source"], item["file_offset"])),
        "overlay_pointer_candidates": all_pointers,
        "roundtrip": {
            "candidate_raw_code_roundtrip": "PASS",
            "confirmed_title_raw": TITLE_PROMPT_RAW.hex().upper(),
            "confirmed_title_text": title_text,
            "confirmed_title_character_count": len(title_codes),
        },
        "next": [
            "Use the per-module callsite reports to resolve fixed records, pointer tables, direct strings, and dynamic inputs.",
            "Add one-character strings and any non-NUL or tokenized consumers only after their reader boundaries are confirmed.",
            "Do not allocate Hangul codes until the resolved/excluded/unknown call-site denominator reaches zero unknowns for the target scope.",
        ],
    }
    output = project / "work/analysis/text_population_001.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "decoder_capacity": report["decoder_capacity"],
        "consumers": {"ARA_calls": len(report["consumers"]["generic_builder_calls"]["ARA.BIN"]), "HAGECHO_calls": len(report["consumers"]["generic_builder_calls"]["HAGECHO.BIN"])},
        "candidate_summary": report["candidate_summary"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
