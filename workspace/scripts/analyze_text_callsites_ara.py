#!/usr/bin/env python3
"""Classify ARA direct calls and enumerate callers hidden behind forwarding APIs."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from collections import Counter
from pathlib import Path

from analyze_text_callsites import decode_nul
from analyze_text_population import GENERIC_STRING_BUILDER, OVERLAY_BASE, jal_word, payload_offset, scan_calls


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ARA_RUNTIME_BASE = 0x7FFD6000
SINGLE_STRING_WRAPPER = 0x800433D0
STRING_ARRAY_RENDERER = 0x80040D34
EXPECTED_DIRECT_CALLS = [
    0x5E3F8, 0x5E5E4, 0x5E604, 0x5E624, 0x5E644, 0x5E664, 0x5E684,
    0x6AD8C, 0x6D3DC, 0x6FC74, 0x6FC94, 0x6FCB4, 0x70164, 0x70194,
    0x701B0, 0x703F4, 0x70424, 0x70454, 0x70470,
]
EXPECTED_WRAPPER_CALLERS_ARA = [0x6A9C8, 0x6A9E0, 0x6A9F8, 0xAA36C]
EXPECTED_WRAPPER_CALLERS_HAGECHO = [
    0x7B5DC, 0x7B620, 0x7B69C, 0x7B6F8, 0x7B724,
    0x1F992C, 0x1F9984, 0x1F99A0, 0x1F99F8, 0x1F9AC4,
    0x227434, 0x227464,
]
EXPECTED_ARRAY_CALLERS_ARA = [
    0x6B0E0, 0x6B538, 0x6B604, 0x6B698, 0x6B6DC, 0x6B748, 0x6B798,
    0x6B7E8, 0x6B838, 0x6B87C, 0x6B8C4, 0x6B93C, 0x6B990, 0x6B9B0,
    0x6B9F4, 0x6BA60, 0x6BC5C, 0x6BDD0, 0x6BEA8, 0x6BF3C, 0x6BFA4,
    0x6BFF4, 0x6C0C4, 0x6C0E0, 0x6C1E0, 0x6C23C, 0x6C2E0,
]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    project = args.project.resolve()

    ara = (project / "extracted/ARA.BIN").read_bytes()
    hagecho = (project / "extracted/HAGECHO.BIN").read_bytes()
    blocks = json.loads((project / "work/analysis/hagecho_blocks.json").read_text(encoding="utf-8"))["blocks"]
    encoding = json.loads((project / "workspace/config/font_encoding.json").read_text(encoding="utf-8"))
    mapping = {int(item["sjis"], 16): item for item in encoding["mappings"]}

    if 0x800430DC - ARA_RUNTIME_BASE != 0x6D0DC:
        raise AssertionError("ARA runtime mapping anchor changed")
    if scan_calls(ara, GENERIC_STRING_BUILDER) != EXPECTED_DIRECT_CALLS:
        raise ValueError("ARA direct generic-renderer call inventory changed")

    wrapper_ara = scan_calls(ara, SINGLE_STRING_WRAPPER)
    wrapper_hagecho = scan_calls(hagecho, SINGLE_STRING_WRAPPER)
    array_ara = scan_calls(ara, STRING_ARRAY_RENDERER)
    if wrapper_ara != EXPECTED_WRAPPER_CALLERS_ARA:
        raise ValueError("ARA single-string wrapper caller inventory changed")
    if wrapper_hagecho != EXPECTED_WRAPPER_CALLERS_HAGECHO:
        raise ValueError("HAGECHO single-string wrapper caller inventory changed")
    if array_ara != EXPECTED_ARRAY_CALLERS_ARA:
        raise ValueError("ARA string-array renderer caller inventory changed")

    def ara_source(runtime: int, limit: int = 0x80) -> dict:
        offset = runtime - ARA_RUNTIME_BASE
        if not 0 <= offset < len(ara):
            raise ValueError(f"ARA runtime address outside file: 0x{runtime:08X}")
        return {"runtime_address": f"0x{runtime:08X}", "ara_offset": offset, **decode_nul(ara, offset, mapping, limit)}

    digit_runtime = 0x800A3BF0
    digit_offset = digit_runtime - ARA_RUNTIME_BASE
    digit_entries = []
    for index in range(10):
        raw = ara[digit_offset + index * 2 : digit_offset + index * 2 + 2]
        code = int.from_bytes(raw, "big")
        if code not in mapping or mapping[code]["character"] != chr(ord("０") + index):
            raise ValueError("ARA formatted-digit table changed")
        digit_entries.append({
            "index": index,
            "runtime_address": f"0x{digit_runtime + index * 2:08X}",
            "ara_offset": digit_offset + index * 2,
            "raw_hex": raw.hex().upper(),
            "codes": [f"{code:04X}"],
            "unmapped_codes": [],
            "text": mapping[code]["character"],
            "character_count": 1,
        })

    literal_specs = [
        ("difficulty_label", 0x8001000C, "なんいど："),
        ("system_level_label", 0x80010018, "シスレベ："),
        ("game_level_label", 0x80010024, "ゲーレベ："),
        ("continue_label", 0x80010030, "コンティ："),
        ("life_percent_label", 0x8001003C, "ライフ　：　　　％"),
        ("total_percent_label", 0x80010050, "トータル：　　　％"),
        ("restart_question", 0x800A61A4, "リスタート？"),
        ("yes", 0x800A61B4, "ＹＥＳ"),
        ("no", 0x800A61BC, "ＮＯ"),
        ("taunt_1a", 0x800A6364, "まだまだあまいな"),
        ("taunt_1b", 0x800A6378, "ちょっとてかげんしてやろう"),
        ("taunt_2a", 0x800A6394, "なかなかやるな"),
        ("taunt_2b", 0x800A63A4, "ちょっとホンキだしてやるか"),
        ("tutorial_smiley_1", 0x800A63C0, "スマイリーボールを"),
        ("tutorial_smiley_2", 0x800A63D4, "つかいこなそう！"),
        ("tutorial_item_1", 0x800A63E8, "アイテムを"),
        ("tutorial_item_2", 0x800A63F4, "つかいこなそう！"),
        ("tutorial_enemy_1", 0x800A6408, "たくさんのテキと"),
        ("tutorial_enemy_2", 0x800A641C, "たたかおう！"),
    ]
    literals = []
    for entry_id, runtime, expected_text in literal_specs:
        decoded = ara_source(runtime)
        if decoded["text"] != expected_text:
            raise ValueError(f"ARA literal changed: {entry_id}")
        literals.append({"entry_id": entry_id, **decoded})

    call_specs = [
        (0x5E3F8, "stack_formatted_single_digit", "formatted_digit_table", "resolved_static_source"),
        (0x5E5E4, "direct_literal", "difficulty_label", "resolved_static_source"),
        (0x5E604, "direct_literal", "system_level_label", "resolved_static_source"),
        (0x5E624, "direct_literal", "game_level_label", "resolved_static_source"),
        (0x5E644, "direct_literal", "continue_label", "resolved_static_source"),
        (0x5E664, "direct_literal", "life_percent_label", "resolved_static_source"),
        (0x5E684, "direct_literal", "total_percent_label", "resolved_static_source"),
        (0x6AD8C, "forwarding_pointer_array_renderer", "caller_supplied_pointer_array", "forwarding_api_callers_open"),
        (0x6D3DC, "forwarding_single_string_wrapper", "caller_supplied_string", "forwarding_api_callers_open"),
        (0x6FC74, "direct_literal", "restart_question", "resolved_static_source"),
        (0x6FC94, "direct_literal", "yes", "resolved_static_source"),
        (0x6FCB4, "direct_literal", "no", "resolved_static_source"),
        (0x70164, "direct_literal", "taunt_1a", "resolved_static_source"),
        (0x70194, "direct_literal", "taunt_2a", "resolved_static_source"),
        (0x701B0, "branch_selected_direct_literal", "taunt_1b_or_2b", "resolved_static_source"),
        (0x703F4, "direct_literal", "tutorial_smiley_1", "resolved_static_source"),
        (0x70424, "direct_literal", "tutorial_item_1", "resolved_static_source"),
        (0x70454, "direct_literal", "tutorial_enemy_1", "resolved_static_source"),
        (0x70470, "branch_selected_direct_literal", "tutorial_second_line", "resolved_static_source"),
    ]
    callsites = []
    for index, (offset, source_kind, source_target, status) in enumerate(call_specs, start=1):
        if ara[offset : offset + 4] != jal_word(GENERIC_STRING_BUILDER):
            raise ValueError(f"call word mismatch at ARA 0x{offset:X}")
        callsites.append({
            "call_id": f"ara-direct-call-{index:02d}",
            "ara_offset": offset,
            "runtime_address": f"0x{ARA_RUNTIME_BASE + offset:08X}",
            "source_kind": source_kind,
            "source_target": source_target,
            "status": status,
        })

    def hagecho_call_record(offset: int) -> dict:
        block = next(item for item in blocks if item["start"] <= offset < item["end"])
        block_data = hagecho[block["start"] : block["end"]]
        module_payload_offset = payload_offset(block_data)
        return {
            "source": "HAGECHO.BIN",
            "file_offset": offset,
            "block_index": block["index"],
            "block_offset": offset - block["start"],
            "payload_offset": offset - block["start"] - module_payload_offset,
            "runtime_address": f"0x{OVERLAY_BASE + offset - block['start'] - module_payload_offset:08X}",
            "status": "unresolved_forwarded_source",
        }

    wrapper_callers = [
        {
            "source": "ARA.BIN",
            "ara_offset": offset,
            "runtime_address": f"0x{ARA_RUNTIME_BASE + offset:08X}",
            "status": "unresolved_forwarded_source",
        }
        for offset in wrapper_ara
    ] + [hagecho_call_record(offset) for offset in wrapper_hagecho]
    array_callers = [
        {
            "source": "ARA.BIN",
            "ara_offset": offset,
            "runtime_address": f"0x{ARA_RUNTIME_BASE + offset:08X}",
            "status": "unresolved_pointer_array_source",
        }
        for offset in array_ara
    ]

    code_counts: Counter[str] = Counter()
    for entry in digit_entries + literals:
        code_counts.update(entry["codes"])

    report = {
        "schema_version": 1,
        "observation_id": "TEXT-CALLSITES-ARA-001",
        "status": "ARA_DIRECT_CALLS_CLASSIFIED_WITH_FORWARDED_CONSUMERS_OPEN",
        "scope": {
            "revision_bin_sha256": "99dc6c7f748c4882d6f197a0a0a2eb943affc25bb4271deff9bb9bf55859aa54",
            "ara_sha256": sha256(ara),
            "hagecho_sha256": sha256(hagecho),
        },
        "ara_layout": {
            "runtime_base": f"0x{ARA_RUNTIME_BASE:08X}",
            "mapping": "ARA offset = runtime address - 0x7FFD6000",
            "anchor": "decoder 0x800430DC maps to ARA offset 0x6D0DC",
        },
        "direct_call_denominator": {
            "generic_calls": len(callsites),
            "classified": len(callsites),
            "resolved_static_source": sum(item["status"] == "resolved_static_source" for item in callsites),
            "forwarding_api_calls": sum(item["status"] == "forwarding_api_callers_open" for item in callsites),
            "unknown_callsite_shape": 0,
        },
        "confirmed_static_population": {
            "entry_count": len(digit_entries) + len(literals),
            "digit_table": {
                "runtime_address": f"0x{digit_runtime:08X}",
                "ara_offset": digit_offset,
                "entry_count": len(digit_entries),
                "entries": digit_entries,
            },
            "literals": literals,
            "unique_code_count": len(code_counts),
            "code_occurrences": [{"sjis": code, "count": code_counts[code]} for code in sorted(code_counts)],
            "consumer_confirmed_decoder_fallback_codes": sorted({code for item in literals for code in item["unmapped_codes"]}),
        },
        "direct_callsites": callsites,
        "forwarding_consumer_expansion": {
            "single_string_wrapper": {
                "runtime_address": f"0x{SINGLE_STRING_WRAPPER:08X}",
                "direct_generic_call_ara_offset": 0x6D3DC,
                "caller_count": len(wrapper_callers),
                "callers": wrapper_callers,
            },
            "pointer_array_renderer": {
                "runtime_address": f"0x{STRING_ARRAY_RENDERER:08X}",
                "direct_generic_call_ara_offset": 0x6AD8C,
                "caller_count": len(array_callers),
                "callers": array_callers,
            },
            "new_open_caller_denominator": len(wrapper_callers) + len(array_callers),
            "resolved": 0,
            "unknown": len(wrapper_callers) + len(array_callers),
            "reason": "The two ARA direct call sites forward caller-owned strings or pointer arrays; their callers are independent text consumers.",
        },
        "roundtrip": {
            "all_19_direct_call_words_present": "PASS",
            "all_confirmed_static_raw_codes_preserved": "PASS",
            "forwarding_caller_inventories_fixed": "PASS",
        },
        "next": [
            "Classify the 16 callers of the single-string wrapper.",
            "Classify the 27 callers of the pointer-array renderer and preserve array counts, order, and null semantics.",
            "Do not declare the text population closed while any of the 43 forwarded consumers remain unknown.",
        ],
    }
    output = project / "work/analysis/text_callsites_ara_001.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "direct_call_denominator": report["direct_call_denominator"],
        "confirmed_static_entries": report["confirmed_static_population"]["entry_count"],
        "forwarding_expansion": {
            "single_string_wrapper_callers": len(wrapper_callers),
            "pointer_array_renderer_callers": len(array_callers),
            "new_open_caller_denominator": len(wrapper_callers) + len(array_callers),
        },
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
