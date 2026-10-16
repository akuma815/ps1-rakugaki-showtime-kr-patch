#!/usr/bin/env python3
"""Classify all callers and source arrays for renderer 0x80040D34."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from collections import Counter
from pathlib import Path

from analyze_text_callsites import decode_nul
from analyze_text_population import jal_word, scan_calls


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ARA_RUNTIME_BASE = 0x7FFD6000
ARRAY_RENDERER = 0x80040D34
EXPECTED_CALLERS = [
    0x6B0E0, 0x6B538, 0x6B604, 0x6B698, 0x6B6DC, 0x6B748, 0x6B798,
    0x6B7E8, 0x6B838, 0x6B87C, 0x6B8C4, 0x6B93C, 0x6B990, 0x6B9B0,
    0x6B9F4, 0x6BA60, 0x6BC5C, 0x6BDD0, 0x6BEA8, 0x6BF3C, 0x6BFA4,
    0x6BFF4, 0x6C0C4, 0x6C0E0, 0x6C1E0, 0x6C23C, 0x6C2E0,
]
ARRAY_BASES = list(range(0x800A4CDC, 0x800A4E5C, 12))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    output = args.output or project / "work/analysis/text_array_callers_001.json"

    ara = (project / "extracted/ARA.BIN").read_bytes()
    encoding = json.loads((project / "workspace/config/font_encoding.json").read_text(encoding="utf-8"))
    mapping = {int(item["sjis"], 16): item for item in encoding["mappings"]}

    if scan_calls(ara, ARRAY_RENDERER) != EXPECTED_CALLERS:
        raise ValueError("pointer-array renderer caller inventory changed")
    for offset in EXPECTED_CALLERS:
        if ara[offset : offset + 4] != jal_word(ARRAY_RENDERER):
            raise ValueError(f"array-renderer call word mismatch at ARA 0x{offset:X}")

    def ara_offset(runtime: int) -> int:
        offset = runtime - ARA_RUNTIME_BASE
        if not 0 <= offset < len(ara):
            raise ValueError(f"ARA runtime address outside file: 0x{runtime:08X}")
        return offset

    def source(runtime: int) -> dict:
        offset = ara_offset(runtime)
        return {
            "runtime_address": f"0x{runtime:08X}",
            "ara_offset": offset,
            **decode_nul(ara, offset, mapping, 0x100),
        }

    # Consumer invariants: a0 is a little-endian pointer array, a1 is its
    # maximum count, and a NULL slot ends iteration before that maximum.
    renderer_anchors = {
        "array_pointer_load": (0x80040D70, 0x8C440000),
        "null_terminates": (0x80040D78, 0x1080000A),
        "count_compare": (0x80040D98, 0x0211102B),
        "generic_renderer_call": (0x80040D8C, 0x0C010CD2),
    }
    for name, (runtime, expected_word) in renderer_anchors.items():
        actual = struct.unpack_from("<I", ara, ara_offset(runtime))[0]
        if actual != expected_word:
            raise ValueError(f"array renderer anchor changed: {name}")

    arrays = []
    nonnull_targets: list[int] = []
    source_by_target: dict[int, dict] = {}
    for array_index, runtime in enumerate(ARRAY_BASES):
        slots = []
        for slot_index in range(3):
            pointer_offset = ara_offset(runtime) + slot_index * 4
            target = struct.unpack_from("<I", ara, pointer_offset)[0]
            item = {
                "slot_index": slot_index,
                "pointer_ara_offset": pointer_offset,
                "pointer_raw": f"0x{target:08X}",
                "is_null": target == 0,
            }
            if target:
                decoded = source_by_target.setdefault(target, source(target))
                item["source"] = decoded
                nonnull_targets.append(target)
            slots.append(item)
        first_null = next((item["slot_index"] for item in slots if item["is_null"]), None)
        effective_count = first_null if first_null is not None else 3
        arrays.append({
            "array_id": f"memory-card-message-array-{array_index:02d}",
            "array_index": array_index,
            "runtime_address": f"0x{runtime:08X}",
            "ara_offset": ara_offset(runtime),
            "slot_count": 3,
            "effective_count_before_null": effective_count,
            "slots": slots,
        })

    if len(arrays) != 32 or len(nonnull_targets) != 54 or len(source_by_target) != 41:
        raise AssertionError("array/string population changed")
    duplicates = Counter(nonnull_targets)
    duplicate_targets = {target: count for target, count in duplicates.items() if count > 1}
    if sum(duplicates.values()) - len(duplicates) != 13:
        raise AssertionError("duplicate pointer count changed")

    # Check that no pointer enters the middle of another confirmed string.
    internal_entries = []
    for target, decoded in source_by_target.items():
        for owner, owner_decoded in source_by_target.items():
            if owner < target < owner + owner_decoded["byte_length"]:
                internal_entries.append({"target": f"0x{target:08X}", "owner": f"0x{owner:08X}"})
    if internal_entries:
        raise ValueError("unexpected internal string entry in memory-card arrays")

    field20_arrays = [
        0x800A4D54, 0x800A4D84, 0x800A4D90, 0x800A4D9C, 0x800A4DA8,
        0x800A4DB4, 0x800A4DD8, 0x800A4DE4, 0x800A4DF0, 0x800A4DFC,
        0x800A4E20,
    ]
    field24_arrays = [0x800A4D90]
    helper_arrays = [
        0x800A4D48, 0x800A4D84, 0x800A4DA8, 0x800A4DC0, 0x800A4DCC,
        0x800A4DF0, 0x800A4DFC, 0x800A4E20, 0x800A4E2C, 0x800A4E38,
        0x800A4E50,
    ]
    shared_420c4_arrays = [
        0x800A4CE8, 0x800A4CF4, 0x800A4D00, 0x800A4D3C, 0x800A4D6C,
        0x800A4D78, 0x800A4D90,
    ]

    specs = [
        (0x6B0E0, "forwarding_helper", helper_arrays),
        (0x6B538, "branch_joined_static_array", [0x800A4D00, 0x800A4D6C, 0x800A4D78]),
        (0x6B604, "direct_static_array", [0x800A4D00]),
        (0x6B698, "direct_static_array", [0x800A4D54]),
        (0x6B6DC, "direct_static_array", [0x800A4D60]),
        (0x6B748, "direct_static_array", [0x800A4CE8]),
        (0x6B798, "direct_static_array", [0x800A4D0C]),
        (0x6B7E8, "direct_static_array", [0x800A4CF4]),
        (0x6B838, "direct_static_array", [0x800A4CDC]),
        (0x6B87C, "direct_static_array", [0x800A4D18]),
        (0x6B8C4, "direct_static_array", [0x800A4D24]),
        (0x6B93C, "direct_static_array", [0x800A4D30]),
        (0x6B990, "direct_static_array", [0x800A4D3C]),
        (0x6B9B0, "direct_static_array", [0x800A4D48]),
        (0x6B9F4, "state_field_plus_0x20", field20_arrays),
        (0x6BA60, "state_field_plus_0x20", field20_arrays),
        (0x6BC5C, "state_field_plus_0x20", field20_arrays),
        (0x6BDD0, "state_field_plus_0x24", field24_arrays),
        (0x6BEA8, "direct_static_array", [0x800A4E44]),
        (0x6BF3C, "direct_static_array", [0x800A4E14]),
        (0x6BFA4, "direct_static_array", [0x800A4DD8]),
        (0x6BFF4, "branch_joined_static_array", [0x800A4D30, 0x800A4D90]),
        (0x6C0C4, "shared_branch_join_static_array", shared_420c4_arrays),
        (0x6C0E0, "direct_static_array", [0x800A4D3C]),
        (0x6C1E0, "state_field_plus_0x20", field20_arrays),
        (0x6C23C, "state_field_plus_0x20", field20_arrays),
        (0x6C2E0, "direct_static_array", [0x800A4E08]),
    ]
    callsites = []
    for index, (offset, source_kind, possible_arrays) in enumerate(specs, start=1):
        unknown = sorted(set(possible_arrays) - set(ARRAY_BASES))
        if unknown:
            raise ValueError(f"callsite references arrays outside catalog: {unknown}")
        callsites.append({
            "call_id": f"array-renderer-call-{index:02d}",
            "ara_offset": offset,
            "runtime_address": f"0x{ARA_RUNTIME_BASE + offset:08X}",
            "source_kind": source_kind,
            "maximum_count": 3,
            "possible_array_runtime_addresses": [f"0x{item:08X}" for item in possible_arrays],
            "status": "resolved_static_array_source",
        })

    # Every array in the contiguous catalog is tied to at least one direct,
    # helper, state-field, or branch-join consumer path.
    covered = {value for _, _, values in specs for value in values}
    if covered != set(ARRAY_BASES):
        missing = sorted(set(ARRAY_BASES) - covered)
        extra = sorted(covered - set(ARRAY_BASES))
        raise AssertionError(f"array coverage mismatch; missing={missing}, extra={extra}")

    report = {
        "schema_version": 1,
        "observation_id": "TEXT-ARRAY-CALLERS-001",
        "status": "ALL_POINTER_ARRAY_RENDERER_CALLERS_RESOLVED",
        "scope": {
            "revision_bin_sha256": "99dc6c7f748c4882d6f197a0a0a2eb943affc25bb4271deff9bb9bf55859aa54",
            "ara_sha256": sha256(ara),
            "renderer_runtime": f"0x{ARRAY_RENDERER:08X}",
        },
        "renderer_contract": {
            "array_pointer_width": 4,
            "pointer_endian": "little",
            "maximum_count_argument": 3,
            "null_semantics": "first NULL pointer terminates rendering before maximum count",
            "line_step_pixels": 16,
            "code_anchors": {key: {"runtime_address": f"0x{value[0]:08X}", "word": f"0x{value[1]:08X}"} for key, value in renderer_anchors.items()},
        },
        "denominator": {
            "callers_total": 27,
            "classified": 27,
            "resolved_static_array_source": 27,
            "unknown": 0,
        },
        "callsites": callsites,
        "array_catalog": {
            "runtime_range": "0x800A4CDC-0x800A4E5C",
            "array_count": len(arrays),
            "array_stride": 12,
            "slots_per_array": 3,
            "total_slots": len(arrays) * 3,
            "nonnull_slots": len(nonnull_targets),
            "null_slots": len(arrays) * 3 - len(nonnull_targets),
            "unique_string_coordinates": len(source_by_target),
            "duplicate_pointer_references": len(nonnull_targets) - len(source_by_target),
            "internal_string_entries": internal_entries,
            "duplicate_targets": [
                {"runtime_address": f"0x{target:08X}", "reference_count": count, "source": source_by_target[target]}
                for target, count in sorted(duplicate_targets.items())
            ],
            "arrays": arrays,
        },
        "population_delta": {
            "new_unique_static_source_entries": 41,
            "previous_confirmed_unique_static_source_entries": 208,
            "confirmed_unique_static_source_entries": 249,
        },
        "forwarded_consumer_denominator": {
            "single_string_wrapper_callers": {"total": 16, "resolved": 16, "unknown": 0},
            "pointer_array_renderer_callers": {"total": 27, "resolved": 27, "unknown": 0},
            "total": 43,
            "resolved": 43,
            "unknown": 0,
        },
        "roundtrip": {
            "all_27_call_words_present": "PASS",
            "all_32_array_records_preserved": "PASS",
            "all_96_pointer_slots_preserved": "PASS",
            "duplicates_and_nulls_preserved": "PASS",
            "original_files_modified": False,
        },
        "next": [
            "Audit one-character and alternate text readers before declaring the full text population closed.",
            "Keep Hangul allocation blocked until the remaining reader audit confirms no hidden code consumers.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {output}")
    print("array callers: 27; resolved: 27; unknown: 0")
    print("arrays: 32; slots: 96; nonnull: 54; unique strings: 41")
    print("confirmed unique static coordinates: 249")


if __name__ == "__main__":
    main()
