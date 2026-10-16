#!/usr/bin/env python3
"""Resolve the two runtime producers left open by wrapper caller analysis."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

from analyze_text_callsites import decode_nul
from analyze_text_population import OVERLAY_BASE, payload_offset


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ARA_RUNTIME_BASE = 0x7FFD6000


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    output = args.output or project / "work/analysis/text_wrapper_producers_001.json"

    ara = (project / "extracted/ARA.BIN").read_bytes()
    hagecho = (project / "extracted/HAGECHO.BIN").read_bytes()
    blocks = json.loads((project / "work/analysis/hagecho_blocks.json").read_text(encoding="utf-8"))["blocks"]
    encoding = json.loads((project / "workspace/config/font_encoding.json").read_text(encoding="utf-8"))
    mapping = {int(item["sjis"], 16): item for item in encoding["mappings"]}

    def ara_offset(runtime: int) -> int:
        offset = runtime - ARA_RUNTIME_BASE
        if not 0 <= offset < len(ara):
            raise ValueError(f"ARA address outside file: 0x{runtime:08X}")
        return offset

    def ara_source(runtime: int, limit: int = 0x100) -> dict:
        offset = ara_offset(runtime)
        return {
            "runtime_address": f"0x{runtime:08X}",
            "ara_offset": offset,
            **decode_nul(ara, offset, mapping, limit),
        }

    block4 = blocks[4]
    block4_wrapper = payload_offset(hagecho[block4["start"] : block4["end"]])
    if block4_wrapper != 0:
        raise ValueError("block 4 payload mapping changed")

    def block4_offset(runtime: int) -> int:
        offset = block4["start"] + runtime - OVERLAY_BASE
        if not block4["start"] <= offset < block4["end"]:
            raise ValueError(f"block 4 address outside module: 0x{runtime:08X}")
        return offset

    def block4_source(runtime: int, limit: int = 0x100) -> dict:
        offset = block4_offset(runtime)
        return {
            "runtime_address": f"0x{runtime:08X}",
            "file_offset": offset,
            "block_index": 4,
            **decode_nul(hagecho, offset, mapping, limit),
        }

    # Block 4 builds 0x80148270 with strcpy(name_table[index]) + strcat(suffix).
    block4_code_anchors = {
        "name_pointer_load": (0x80147A6C, 0x8C450000),
        "strcpy_call": (0x80147A70, 0x0C028BFD),
        "buffer_argument": (0x80147A74, 0x26048270),
        "strcat_call": (0x80147A80, 0x0C028BB5),
        "suffix_argument": (0x80147A84, 0x24A580A4),
        "wrapper_call": (0x80147ED8, 0x0C010CF4),
    }
    for name, (runtime, expected_word) in block4_code_anchors.items():
        actual = struct.unpack_from("<I", hagecho, block4_offset(runtime))[0]
        if actual != expected_word:
            raise ValueError(f"block 4 producer anchor changed: {name}")

    block4_table_runtime = 0x801481D8
    block4_names = []
    for index in range(17):
        pointer_offset = block4_offset(block4_table_runtime) + index * 4
        target = struct.unpack_from("<I", hagecho, pointer_offset)[0]
        block4_names.append({
            "index": index,
            "pointer_file_offset": pointer_offset,
            "pointer_raw": f"0x{target:08X}",
            **block4_source(target),
        })
    block4_null = struct.unpack_from("<I", hagecho, block4_offset(block4_table_runtime) + 17 * 4)[0]
    if block4_null != 0:
        raise ValueError("block 4 name table no longer has 17 entries plus NULL")
    block4_suffix = block4_source(0x801480A4)
    if block4_suffix["text"] != "　ゲット！！":
        raise ValueError("block 4 assembled suffix changed")

    # The ARA constructor stores callback 0x8008034C at object +0x14 and
    # object+0x44 at +0x24. Two state-machine paths copy exactly one two-byte
    # character from either source table into that inline buffer and append NUL.
    ara_code_anchors = {
        "callback_address": (0x80080580, 0x2442034C),
        "callback_store": (0x80080584, 0xAE020014),
        "inline_buffer_address": (0x80080588, 0x26020044),
        "source_field_store": (0x8008058C, 0xAE020024),
        "callback_source_load": (0x80080360, 0x8C440024),
        "wrapper_call": (0x8008036C, 0x0C010CF4),
        "table_1_address": (0x80080794, 0x24467064),
        "table_1_copy": (0x800807C8, 0xA0820044),
        "table_1_nul": (0x800807E0, 0xA0400044),
        "table_2_address": (0x8008099C, 0x244670BC),
        "table_2_copy": (0x800809D0, 0xA0820044),
        "table_2_nul": (0x800809E8, 0xA0400044),
    }
    for name, (runtime, expected_word) in ara_code_anchors.items():
        actual = struct.unpack_from("<I", ara, ara_offset(runtime))[0]
        if actual != expected_word:
            raise ValueError(f"ARA producer anchor changed: {name}")

    def pointer_table(runtime: int, count: int) -> list[dict]:
        entries = []
        for index in range(count):
            pointer_offset = ara_offset(runtime) + index * 4
            target = struct.unpack_from("<I", ara, pointer_offset)[0]
            entries.append({
                "index": index,
                "pointer_ara_offset": pointer_offset,
                "pointer_raw": f"0x{target:08X}",
                **ara_source(target),
            })
        return entries

    ara_name_entries = pointer_table(0x800A7064, 22)
    if 0x800A7064 + 22 * 4 != 0x800A70BC:
        raise AssertionError("ARA name table boundary changed")
    ara_result_entries = pointer_table(0x800A70BC, 3)
    expected_results = ["ＷＩＮ♪", "ＬＯＳＥ", "ＤＲＡＷ"]
    if [item["text"] for item in ara_result_entries] != expected_results:
        raise ValueError("ARA result table changed")

    report = {
        "schema_version": 1,
        "observation_id": "TEXT-WRAPPER-PRODUCERS-001",
        "status": "ALL_SINGLE_STRING_WRAPPER_SOURCES_RESOLVED",
        "scope": {
            "revision_bin_sha256": "99dc6c7f748c4882d6f197a0a0a2eb943affc25bb4271deff9bb9bf55859aa54",
            "ara_sha256": sha256(ara),
            "hagecho_sha256": sha256(hagecho),
            "supersedes_open_items_from": "TEXT-WRAPPER-CALLERS-001",
        },
        "block4_assembled_buffer": {
            "consumer_callsite_runtime": "0x80147ED8",
            "buffer_runtime": "0x80148270",
            "producer_runtime_range": "0x80147A6C-0x80147A84",
            "operation": "strcpy(buffer, name_table[index]); strcat(buffer, suffix)",
            "name_table": {
                "runtime_address": f"0x{block4_table_runtime:08X}",
                "entry_count": len(block4_names),
                "null_sentinel_index": 17,
                "entries": block4_names,
            },
            "suffix": block4_suffix,
            "code_anchors": {key: {"runtime_address": f"0x{value[0]:08X}", "word": f"0x{value[1]:08X}"} for key, value in block4_code_anchors.items()},
            "status": "resolved_static_source_table_and_suffix",
        },
        "ara_inline_character_buffer": {
            "consumer_callsite_runtime": "0x8008036C",
            "constructor_runtime": "0x80080534",
            "callback_runtime": "0x8008034C",
            "source_field": "object +0x24 points to object +0x44",
            "copy_rule": "copy exactly two source bytes for one character, then append NUL",
            "name_team_table": {
                "runtime_address": "0x800A7064",
                "entry_count": len(ara_name_entries),
                "entries": ara_name_entries,
            },
            "result_table": {
                "runtime_address": "0x800A70BC",
                "entry_count": len(ara_result_entries),
                "entries": ara_result_entries,
            },
            "code_anchors": {key: {"runtime_address": f"0x{value[0]:08X}", "word": f"0x{value[1]:08X}"} for key, value in ara_code_anchors.items()},
            "status": "resolved_static_source_tables_via_inline_character_buffer",
        },
        "wrapper_denominator_after_resolution": {
            "callers_total": 16,
            "classified": 16,
            "resolved_static_source": 16,
            "producer_unresolved": 0,
            "unknown_callsite_shape": 0,
        },
        "population_delta": {
            "new_unique_static_source_entries": 43,
            "breakdown": {"block4_names": 17, "block4_suffix": 1, "ara_names_teams": 22, "ara_results": 3},
            "previous_confirmed_unique_static_source_entries": 165,
            "confirmed_unique_static_source_entries": 208,
        },
        "remaining_forwarded_consumers": {
            "wrapper_producers_unresolved": 0,
            "pointer_array_renderer_callers_unresolved": 27,
            "total_unresolved": 27,
        },
        "roundtrip": {
            "all_producer_code_anchors_match": "PASS",
            "all_static_sources_decode_with_nul_boundaries": "PASS",
            "source_table_boundaries_fixed": "PASS",
            "original_files_modified": False,
        },
        "next": [
            "Classify all 27 callers of pointer-array renderer 0x80040D34.",
            "Preserve pointer-array counts, ordering, duplicate pointers, and null semantics.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {output}")
    print("wrapper callers: 16; resolved static source: 16; open: 0")
    print("new unique static coordinates: 43; confirmed total: 208")
    print("remaining forwarded consumers: 27")


if __name__ == "__main__":
    main()
