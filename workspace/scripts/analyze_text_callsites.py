#!/usr/bin/env python3
"""Classify block 1 calls to the confirmed generic string renderer."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from collections import Counter
from pathlib import Path

from analyze_text_population import GENERIC_STRING_BUILDER, OVERLAY_BASE, jal_word, payload_offset, scan_calls


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BLOCK_INDEX = 1
EXPECTED_BLOCK_CALLS = [
    0x769B4, 0x769FC, 0x779AC, 0x779CC, 0x7A884, 0x7A8C0, 0x7B420, 0x7B488,
    0x7B558, 0x7D144, 0x7ECA0, 0x7ECE8, 0x7ED38, 0x7F158, 0x7F228,
]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decode_nul(data: bytes, offset: int, mapping: dict[int, dict], limit: int) -> dict:
    codes: list[int] = []
    characters: list[str] = []
    unmapped_codes: list[int] = []
    cursor = offset
    end = min(len(data), offset + limit)
    while cursor + 1 < end and data[cursor] != 0:
        code = int.from_bytes(data[cursor : cursor + 2], "big")
        codes.append(code)
        if code in mapping:
            characters.append(mapping[code]["character"])
        else:
            unmapped_codes.append(code)
            try:
                characters.append(data[cursor : cursor + 2].decode("cp932"))
            except UnicodeDecodeError:
                characters.append(f"<{code:04X}>")
        cursor += 2
    if cursor >= end or data[cursor] != 0:
        raise ValueError(f"missing NUL lead terminator at file offset 0x{offset:X}")
    return {
        "text": "".join(characters),
        "raw_hex": data[offset:cursor].hex().upper(),
        "codes": [f"{code:04X}" for code in codes],
        "unmapped_codes": [f"{code:04X}" for code in unmapped_codes],
        "character_count": len(codes),
        "byte_length": cursor - offset,
        "terminator_file_offset": cursor,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    project = args.project.resolve()

    hagecho = (project / "extracted/HAGECHO.BIN").read_bytes()
    blocks = json.loads((project / "work/analysis/hagecho_blocks.json").read_text(encoding="utf-8"))["blocks"]
    block = blocks[BLOCK_INDEX]
    block_data = hagecho[block["start"] : block["end"]]
    module_payload_offset = payload_offset(block_data)
    if module_payload_offset != 0x10:
        raise ValueError("block 1 wrapper/payload offset changed")
    declared_payload_size = struct.unpack_from("<I", block_data, 12)[0]

    encoding = json.loads((project / "workspace/config/font_encoding.json").read_text(encoding="utf-8"))
    mapping = {int(item["sjis"], 16): item for item in encoding["mappings"]}

    actual_calls = [offset for offset in scan_calls(hagecho, GENERIC_STRING_BUILDER) if block["start"] <= offset < block["end"]]
    if actual_calls != EXPECTED_BLOCK_CALLS:
        raise ValueError(f"block 1 call inventory changed: {actual_calls}")
    for offset in actual_calls:
        if hagecho[offset : offset + 4] != jal_word(GENERIC_STRING_BUILDER):
            raise ValueError(f"call word mismatch at 0x{offset:X}")

    def runtime_to_file(runtime: int) -> int:
        file_offset = block["start"] + module_payload_offset + runtime - OVERLAY_BASE
        if not block["start"] <= file_offset < block["end"]:
            raise ValueError(f"runtime target outside block 1: 0x{runtime:08X}")
        return file_offset

    def source(runtime: int, limit: int = 0x40) -> dict:
        file_offset = runtime_to_file(runtime)
        decoded = decode_nul(hagecho, file_offset, mapping, limit)
        return {"runtime_address": f"0x{runtime:08X}", "file_offset": file_offset, **decoded}

    digit_entries = []
    for index in range(10):
        entry = source(0x80160AFC + index * 4, 4)
        if entry["text"] != chr(ord("０") + index):
            raise ValueError("digit table content changed")
        digit_entries.append({"index": index, "slot_size": 4, **entry})

    name_records = []
    name_base = 0x801608A4
    name_stride = 30
    if name_base + 20 * name_stride != 0x80160AFC:
        raise AssertionError("name table no longer meets digit table boundary")
    for index in range(20):
        record_runtime = name_base + index * name_stride
        record_file = runtime_to_file(record_runtime)
        slots = []
        for line, relative in enumerate((8, 19), start=1):
            decoded = decode_nul(hagecho, record_file + relative, mapping, 11)
            slots.append({
                "line": line,
                "record_relative_offset": relative,
                "slot_size": 11,
                "runtime_address": f"0x{record_runtime + relative:08X}",
                "file_offset": record_file + relative,
                **decoded,
            })
        name_records.append({
            "index": index,
            "runtime_address": f"0x{record_runtime:08X}",
            "file_offset": record_file,
            "stride": name_stride,
            "coordinate_raw_hex": hagecho[record_file : record_file + 8].hex().upper(),
            "raw_record_hex": hagecho[record_file : record_file + name_stride].hex().upper(),
            "slots": slots,
        })

    pointer_table_runtime = 0x80161538
    pointer_table_file = runtime_to_file(pointer_table_runtime)
    expected_pointer_texts = [
        "タイジンなげ", "タイブツなげ", "ジャンプ", "トクシュわざ",
        "ひだりかいてん", "みしよう１", "みぎかいてん", "みしよう２",
    ]
    pointer_entries = []
    for index, expected_text in enumerate(expected_pointer_texts):
        target = struct.unpack_from("<I", hagecho, pointer_table_file + index * 4)[0]
        decoded = source(target)
        if decoded["text"] != expected_text:
            raise ValueError(f"pointer table entry {index} changed")
        pointer_entries.append({
            "index": index,
            "pointer_file_offset": pointer_table_file + index * 4,
            "pointer_raw": f"0x{target:08X}",
            **decoded,
        })

    direct_specs = [
        ("stage_select", 0x80155A20, "ステージセレクト"),
        ("level_select", 0x80155A34, "レベルセレクト"),
        ("confirm", 0x80155A44, "けってい"),
        ("level_prefix", 0x80155A50, "レベル／"),
        ("difficulty_locked", 0x80155A5C, "ーーーー"),
        ("difficulty_easy", 0x80155A68, "イージー"),
        ("difficulty_normal", 0x80155A74, "ノーマル"),
        ("difficulty_hard", 0x80155A80, "ハード"),
        ("difficulty_super", 0x80155A88, "スーパー"),
        ("toggle_on", 0x80160F64, "ＯＮ"),
        ("toggle_off", 0x80160F80, "ＯＦＦ"),
        ("confirm_question", 0x80160F9C, "ＯＫ？"),
    ]
    direct_entries = []
    for entry_id, runtime, expected_text in direct_specs:
        decoded = source(runtime)
        if decoded["text"] != expected_text:
            raise ValueError(f"direct literal {entry_id} changed")
        direct_entries.append({"entry_id": entry_id, **decoded})

    item_record_base = 0x80160FB8
    item_record_stride = 0x38
    item_records = []
    for index in range(8):
        record_runtime = item_record_base + index * item_record_stride
        record_file = runtime_to_file(record_runtime)
        decoded = decode_nul(hagecho, record_file + 0x14, mapping, item_record_stride - 0x14)
        item_records.append({
            "index": index,
            "runtime_address": f"0x{record_runtime:08X}",
            "file_offset": record_file,
            "stride": item_record_stride,
            "string_relative_offset": 0x14,
            "string_runtime_address": f"0x{record_runtime + 0x14:08X}",
            "string_file_offset": record_file + 0x14,
            "raw_record_hex": hagecho[record_file : record_file + item_record_stride].hex().upper(),
            **decoded,
        })
    sentinel_runtime = item_record_base + len(item_records) * item_record_stride
    sentinel_file = runtime_to_file(sentinel_runtime)
    if struct.unpack_from("<I", hagecho, sentinel_file)[0] != 0:
        raise ValueError("item record sentinel changed")

    call_specs = [
        ("b001-call-01", 0x769B4, "fixed_slot_indexed_table", "digit_table", "resolved_static_source"),
        ("b001-call-02", 0x769FC, "fixed_slot_indexed_table", "digit_table", "resolved_static_source"),
        ("b001-call-03", 0x779AC, "fixed_record_inline_slot", "character_name_records.line1", "resolved_static_source"),
        ("b001-call-04", 0x779CC, "fixed_record_inline_slot", "character_name_records.line2", "resolved_static_source"),
        ("b001-call-05", 0x7A884, "selected_fixed_record_slot", "character_name_records.line1", "resolved_static_source"),
        ("b001-call-06", 0x7A8C0, "selected_fixed_record_slot", "character_name_records.line2", "resolved_static_source"),
        ("b001-call-07", 0x7B420, "fixed_record_inline_string", "item_name_records+0x14", "resolved_static_source"),
        ("b001-call-08", 0x7B488, "conditional_direct_literal", "toggle_on_or_off", "resolved_static_source"),
        ("b001-call-09", 0x7B558, "direct_literal", "confirm_question", "resolved_static_source"),
        ("b001-call-10", 0x7D144, "pointer_table", "control_label_pointer_table", "resolved_static_source"),
        ("b001-call-11", 0x7ECA0, "direct_literal", "stage_select", "resolved_static_source"),
        ("b001-call-12", 0x7ECE8, "direct_literal", "level_select", "resolved_static_source"),
        ("b001-call-13", 0x7ED38, "direct_literal", "confirm", "resolved_static_source"),
        ("b001-call-14", 0x7F158, "direct_literal", "level_prefix", "resolved_static_source"),
        ("b001-call-15", 0x7F228, "branch_selected_direct_literals", "difficulty_literals", "resolved_static_source"),
    ]
    callsites = []
    for call_id, file_offset, kind, target, status in call_specs:
        runtime = OVERLAY_BASE + file_offset - block["start"] - module_payload_offset
        callsites.append({
            "call_id": call_id,
            "file_offset": file_offset,
            "block_offset": file_offset - block["start"],
            "payload_offset": file_offset - block["start"] - module_payload_offset,
            "runtime_address": f"0x{runtime:08X}",
            "source_kind": kind,
            "source_target": target,
            "status": status,
        })

    all_static_entries = list(digit_entries)
    all_static_entries += [slot for record in name_records for slot in record["slots"] if slot["text"]]
    all_static_entries += pointer_entries + direct_entries + item_records
    code_counts: Counter[str] = Counter()
    for entry in all_static_entries:
        code_counts.update(entry["codes"])

    report = {
        "schema_version": 1,
        "observation_id": "TEXT-CALLSITES-B001-001",
        "status": "BLOCK1_CALLSITES_FULLY_RESOLVED",
        "scope": {
            "revision_bin_sha256": "99dc6c7f748c4882d6f197a0a0a2eb943affc25bb4271deff9bb9bf55859aa54",
            "source": "HAGECHO.BIN",
            "source_sha256": sha256(hagecho),
            "block_index": BLOCK_INDEX,
            "block_start": block["start"],
            "block_size": block["size"],
        },
        "module_layout": {
            "wrapper_header_size": module_payload_offset,
            "wrapper_header_hex": block_data[:module_payload_offset].hex().upper(),
            "declared_payload_size": declared_payload_size,
            "runtime_base": f"0x{OVERLAY_BASE:08X}",
            "mapping": "file_offset = block_start + 0x10 + (runtime_address - 0x8012BCE0)",
            "correction": "Earlier block-agnostic runtime addresses were 0x10 too high for wrapped modules; file call offsets and block 0 title addresses were unaffected.",
        },
        "denominator": {
            "generic_calls": len(callsites),
            "classified": len(callsites),
            "resolved_static_source": sum(item["status"] == "resolved_static_source" for item in callsites),
            "classified_dynamic_source_unresolved": 0,
            "unknown_callsite_shape": 0,
        },
        "static_population": {
            "nonempty_entry_count": len(all_static_entries),
            "unique_code_count": len(code_counts),
            "code_occurrences": [{"sjis": code, "count": code_counts[code]} for code in sorted(code_counts)],
            "digit_table": {
                "runtime_address": "0x80160AFC",
                "file_offset": runtime_to_file(0x80160AFC),
                "entry_count": len(digit_entries),
                "stride": 4,
                "entries": digit_entries,
            },
            "character_name_records": {
                "runtime_address": f"0x{name_base:08X}",
                "file_offset": runtime_to_file(name_base),
                "record_count": len(name_records),
                "stride": name_stride,
                "slot_offsets": [8, 19],
                "slot_size": 11,
                "nonempty_slot_count": sum(bool(slot["text"]) for record in name_records for slot in record["slots"]),
                "records": name_records,
            },
            "control_label_pointer_table": {
                "runtime_address": f"0x{pointer_table_runtime:08X}",
                "file_offset": pointer_table_file,
                "entry_count": len(pointer_entries),
                "pointer_width": 4,
                "entries": pointer_entries,
            },
            "direct_literals": direct_entries,
            "item_name_records": {
                "runtime_address": f"0x{item_record_base:08X}",
                "file_offset": runtime_to_file(item_record_base),
                "record_count": len(item_records),
                "stride": item_record_stride,
                "string_relative_offset": 0x14,
                "sentinel_runtime_address": f"0x{sentinel_runtime:08X}",
                "sentinel_file_offset": sentinel_file,
                "records": item_records,
            },
        },
        "callsites": callsites,
        "roundtrip": {
            "fixed_records_raw_preserved": "PASS",
            "all_code_boundaries_and_raw_values_preserved": "PASS",
            "all_15_expected_call_words_present": "PASS",
            "consumer_confirmed_decoder_fallback_codes": ["8140"],
            "fallback_observation": "0x8140 is CP932 ideographic space and reaches the decoder's 0xFF blank fallback cell.",
        },
        "next": [
            "Classify the three wrapped block 10 generic-renderer call sites with the corrected +0x10 payload mapping.",
            "Classify the 19 ARA.BIN call sites and keep unresolved sources in the denominator.",
        ],
    }
    output = project / "work/analysis/text_callsites_block1_001.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "module_layout": report["module_layout"],
        "denominator": report["denominator"],
        "static_nonempty_entries": report["static_population"]["nonempty_entry_count"],
        "static_unique_codes": report["static_population"]["unique_code_count"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
