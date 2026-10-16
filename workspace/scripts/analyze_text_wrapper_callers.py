#!/usr/bin/env python3
"""Classify all callers of the single-string wrapper at 0x800433D0."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

from analyze_text_callsites import decode_nul
from analyze_text_population import OVERLAY_BASE, jal_word, payload_offset, scan_calls


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ARA_RUNTIME_BASE = 0x7FFD6000
SINGLE_STRING_WRAPPER = 0x800433D0
EXPECTED_ARA_CALLERS = [0x6A9C8, 0x6A9E0, 0x6A9F8, 0xAA36C]
EXPECTED_HAGECHO_CALLERS = [
    0x7B5DC, 0x7B620, 0x7B69C, 0x7B6F8, 0x7B724,
    0x1F992C, 0x1F9984, 0x1F99A0, 0x1F99F8, 0x1F9AC4,
    0x227434, 0x227464,
]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    output = args.output or project / "work/analysis/text_wrapper_callers_001.json"

    ara = (project / "extracted/ARA.BIN").read_bytes()
    hagecho = (project / "extracted/HAGECHO.BIN").read_bytes()
    blocks = json.loads((project / "work/analysis/hagecho_blocks.json").read_text(encoding="utf-8"))["blocks"]
    encoding = json.loads((project / "workspace/config/font_encoding.json").read_text(encoding="utf-8"))
    mapping = {int(item["sjis"], 16): item for item in encoding["mappings"]}

    if scan_calls(ara, SINGLE_STRING_WRAPPER) != EXPECTED_ARA_CALLERS:
        raise ValueError("ARA wrapper caller inventory changed")
    if scan_calls(hagecho, SINGLE_STRING_WRAPPER) != EXPECTED_HAGECHO_CALLERS:
        raise ValueError("HAGECHO wrapper caller inventory changed")
    for offset in EXPECTED_ARA_CALLERS:
        if ara[offset : offset + 4] != jal_word(SINGLE_STRING_WRAPPER):
            raise ValueError(f"ARA call word mismatch at 0x{offset:X}")
    for offset in EXPECTED_HAGECHO_CALLERS:
        if hagecho[offset : offset + 4] != jal_word(SINGLE_STRING_WRAPPER):
            raise ValueError(f"HAGECHO call word mismatch at 0x{offset:X}")

    def ara_source(runtime: int, limit: int = 0x80) -> dict:
        offset = runtime - ARA_RUNTIME_BASE
        return {
            "runtime_address": f"0x{runtime:08X}",
            "ara_offset": offset,
            **decode_nul(ara, offset, mapping, limit),
        }

    def block_info(index: int) -> tuple[dict, int]:
        block = blocks[index]
        wrapper_size = payload_offset(hagecho[block["start"] : block["end"]])
        return block, wrapper_size

    def block_source(index: int, runtime: int, limit: int = 0x100) -> dict:
        block, wrapper_size = block_info(index)
        offset = block["start"] + wrapper_size + runtime - OVERLAY_BASE
        if not block["start"] <= offset < block["end"]:
            raise ValueError(f"block {index} runtime target outside module: 0x{runtime:08X}")
        return {
            "runtime_address": f"0x{runtime:08X}",
            "file_offset": offset,
            "block_index": index,
            **decode_nul(hagecho, offset, mapping, limit),
        }

    def block_runtime_to_file(index: int, runtime: int) -> int:
        block, wrapper_size = block_info(index)
        offset = block["start"] + wrapper_size + runtime - OVERLAY_BASE
        if not block["start"] <= offset < block["end"]:
            raise ValueError(f"block {index} runtime target outside module: 0x{runtime:08X}")
        return offset

    player_slots = []
    player_base = 0x800A486C
    for index, expected in enumerate(("１Ｐ", "２Ｐ", "３Ｐ", "４Ｐ")):
        entry = ara_source(player_base + index * 5, 5)
        if entry["text"] != expected or entry["byte_length"] != 4:
            raise ValueError(f"ARA player slot {index} changed")
        player_slots.append({"index": index, "slot_size": 5, **entry})
    if ara[player_base + 4 * 5 - ARA_RUNTIME_BASE : player_base + 4 * 5 - ARA_RUNTIME_BASE + 4] != bytes.fromhex("01040302"):
        raise ValueError("ARA player-slot boundary/palette sentinel changed")

    ara_labels = []
    for entry_id, runtime, expected in (
        ("continue", 0x800A4884, "つづける"),
        ("quit", 0x800A488E, "やめる"),
    ):
        entry = ara_source(runtime)
        if entry["text"] != expected:
            raise ValueError(f"ARA wrapper literal changed: {entry_id}")
        ara_labels.append({"entry_id": entry_id, **entry})

    block1_digits = []
    for index in range(10):
        entry = block_source(1, 0x80160AFC + index * 4, 4)
        if entry["text"] != chr(ord("０") + index):
            raise ValueError(f"block 1 digit {index} changed")
        block1_digits.append({"index": index, "slot_size": 4, **entry})

    block4_specs = [
        ("all_complete", 0x801480EC, "オールコンプリート"),
        ("congratulations", 0x80148100, "おめでとう！！"),
        ("try_harder", 0x801480B4, "もっとがんばると"),
        ("unlock_others", 0x801480C8, "ほかのキャラもゲットできるよ！！"),
        ("clear_easy", 0x80148034, "レベルイージー　クリアー！"),
        ("clear_normal", 0x80148050, "レベルノーマル　クリアー！"),
        ("clear_hard", 0x8014806C, "レベルハード　クリアー！"),
        ("clear_super", 0x80148088, "レベルスーパー　クリアー！"),
    ]
    block4_entries = []
    for entry_id, runtime, expected in block4_specs:
        entry = block_source(4, runtime)
        if entry["text"] != expected:
            raise ValueError(f"block 4 wrapper literal changed: {entry_id}")
        block4_entries.append({"entry_id": entry_id, **entry})

    table_runtime = 0x801391E8
    table_file = block_runtime_to_file(6, table_runtime)
    block6_entries = []
    for index in range(54):
        target = struct.unpack_from("<I", hagecho, table_file + index * 4)[0]
        entry = block_source(6, target)
        block6_entries.append({
            "index": index,
            "row": index // 3,
            "column": index % 3,
            "pointer_file_offset": table_file + index * 4,
            "pointer_raw": f"0x{target:08X}",
            **entry,
        })
    next_pointer = struct.unpack_from("<I", hagecho, table_file + 54 * 4)[0]
    block6, block6_wrapper = block_info(6)
    block6_runtime_end = OVERLAY_BASE + block6["end"] - block6["start"] - block6_wrapper
    if OVERLAY_BASE <= next_pointer < block6_runtime_end:
        raise ValueError("block 6 result pointer table grew beyond 54 entries")
    tired = block_source(6, 0x80136450)
    if tired["text"] != "つかれたね":
        raise ValueError("block 6 direct wrapper literal changed")

    def ara_call(offset: int, source_kind: str, status: str, source_target: str) -> dict:
        return {
            "source": "ARA.BIN",
            "ara_offset": offset,
            "runtime_address": f"0x{ARA_RUNTIME_BASE + offset:08X}",
            "source_kind": source_kind,
            "source_target": source_target,
            "status": status,
        }

    def hagecho_call(offset: int, source_kind: str, status: str, source_target: str) -> dict:
        block = next(item for item in blocks if item["start"] <= offset < item["end"])
        wrapper_size = payload_offset(hagecho[block["start"] : block["end"]])
        return {
            "source": "HAGECHO.BIN",
            "file_offset": offset,
            "block_index": block["index"],
            "runtime_address": f"0x{OVERLAY_BASE + offset - block['start'] - wrapper_size:08X}",
            "source_kind": source_kind,
            "source_target": source_target,
            "status": status,
        }

    callsites = [
        ara_call(0x6A9C8, "indexed_fixed_stride_slots", "resolved_static_source", "ara_player_slots_4x5"),
        ara_call(0x6A9E0, "direct_literal", "resolved_static_source", "continue"),
        ara_call(0x6A9F8, "direct_literal", "resolved_static_source", "quit"),
        ara_call(0xAA36C, "runtime_structure_field", "producer_unresolved", "caller_structure_plus_0x24"),
        hagecho_call(0x7B5DC, "indexed_digit_table", "resolved_static_source", "block1_digit_table"),
        hagecho_call(0x7B620, "indexed_digit_table", "resolved_static_source", "block1_digit_table"),
        hagecho_call(0x7B69C, "indexed_digit_table", "resolved_static_source", "block1_digit_table"),
        hagecho_call(0x7B6F8, "indexed_digit_table", "resolved_static_source", "block1_digit_table"),
        hagecho_call(0x7B724, "indexed_digit_table", "resolved_static_source", "block1_digit_table"),
        hagecho_call(0x1F992C, "direct_literal", "resolved_static_source", "block4_all_complete"),
        hagecho_call(0x1F9984, "direct_literal", "resolved_static_source", "block4_try_harder"),
        hagecho_call(0x1F99A0, "branch_joined_literal", "resolved_static_source", "block4_congratulations_or_unlock_others"),
        hagecho_call(0x1F99F8, "fixed_runtime_buffer", "producer_unresolved", "0x80148270"),
        hagecho_call(0x1F9AC4, "branch_selected_literal", "resolved_static_source", "block4_difficulty_clear"),
        hagecho_call(0x227434, "indexed_pointer_table_18x3", "resolved_static_source", "block6_result_table"),
        hagecho_call(0x227464, "direct_literal", "resolved_static_source", "block6_tired"),
    ]
    resolved = sum(item["status"] == "resolved_static_source" for item in callsites)
    unresolved = sum(item["status"] == "producer_unresolved" for item in callsites)
    if (len(callsites), resolved, unresolved) != (16, 14, 2):
        raise AssertionError("wrapper classification denominator changed")

    report = {
        "schema_version": 1,
        "observation_id": "TEXT-WRAPPER-CALLERS-001",
        "status": "SINGLE_STRING_WRAPPER_CLASSIFIED_WITH_TWO_PRODUCERS_OPEN",
        "scope": {
            "revision_bin_sha256": "99dc6c7f748c4882d6f197a0a0a2eb943affc25bb4271deff9bb9bf55859aa54",
            "ara_sha256": sha256(ara),
            "hagecho_sha256": sha256(hagecho),
            "wrapper_runtime": f"0x{SINGLE_STRING_WRAPPER:08X}",
        },
        "denominator": {
            "callers_total": 16,
            "classified": 16,
            "resolved_static_source": resolved,
            "producer_unresolved": unresolved,
            "unknown_callsite_shape": 0,
        },
        "callsites": callsites,
        "static_sources": {
            "ara_player_slots": {
                "formula": "0x800A486C + 5 * index; valid runtime indices 0..3",
                "entries": player_slots,
                "boundary_note": "The adjacent word at 0x800A4880 is palette data, not a fifth string.",
            },
            "ara_direct_labels": ara_labels,
            "block1_digit_table_reused": {
                "entries": block1_digits,
                "new_unique_coordinates": 0,
                "reason": "These ten coordinates were already counted by TEXT-CALLSITES-B001-001.",
            },
            "block4_literals": block4_entries,
            "block6_result_table": {
                "runtime_address": f"0x{table_runtime:08X}",
                "shape": "18 rows x 3 columns",
                "entry_count": len(block6_entries),
                "entries": block6_entries,
                "next_word": f"0x{next_pointer:08X}",
            },
            "block6_direct_literal": tired,
        },
        "producer_unresolved": [
            {
                "callsite_runtime": "0x8008036C",
                "source_shape": "a0 = lw 0x24(v0) from caller-supplied runtime structure",
                "static_direct_jal_callers_of_function_0x8008034C": 0,
                "reason": "The enclosing function appears to be reached indirectly; the producer of structure field +0x24 is not yet proven.",
            },
            {
                "callsite_runtime": "0x80147ED8",
                "source_shape": "fixed runtime buffer at 0x80148270",
                "reason": "The file-backed bytes are NUL/zero at load time; the runtime writer/producer is not yet proven.",
            },
        ],
        "population_delta": {
            "new_unique_static_source_entries": 69,
            "breakdown": {"ARA.BIN": 6, "HAGECHO_block_1_reused": 0, "HAGECHO_block_4": 8, "HAGECHO_block_6": 55},
            "previous_confirmed_unique_static_source_entries": 96,
            "confirmed_unique_static_source_entries": 165,
        },
        "remaining_forwarded_consumers": {
            "wrapper_producers_unresolved": 2,
            "pointer_array_renderer_callers_unresolved": 27,
            "total_unresolved": 29,
        },
        "roundtrip": {
            "all_16_call_words_present": "PASS",
            "all_static_sources_decode_with_nul_boundaries": "PASS",
            "block6_pointer_table_54_entries": "PASS",
            "original_files_modified": False,
        },
        "next": [
            "Trace the producer of ARA callback structure field +0x24.",
            "Trace the writer of HAGECHO block 4 runtime buffer 0x80148270.",
            "Classify the 27 callers of pointer-array renderer 0x80040D34.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {output}")
    print(f"wrapper callers: {len(callsites)}; static {resolved}; producer-open {unresolved}")
    print("new unique static coordinates: 69; confirmed total: 165")
    print("remaining forwarded consumers: 29")


if __name__ == "__main__":
    main()
