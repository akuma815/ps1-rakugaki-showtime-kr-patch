#!/usr/bin/env python3
"""Resolve the three block 10 generic-renderer calls and their ARA literals."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

from analyze_text_callsites import decode_nul
from analyze_text_population import GENERIC_STRING_BUILDER, OVERLAY_BASE, jal_word, payload_offset, scan_calls


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BLOCK_INDEX = 10
ARA_RUNTIME_BASE = 0x7FFD6000
EXPECTED = [
    (0x2A94FC, 0x800A61A4, "リスタート？"),
    (0x2A951C, 0x800A61B4, "ＹＥＳ"),
    (0x2A953C, 0x800A61BC, "ＮＯ"),
]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    project = args.project.resolve()

    hagecho = (project / "extracted/HAGECHO.BIN").read_bytes()
    ara = (project / "extracted/ARA.BIN").read_bytes()
    blocks = json.loads((project / "work/analysis/hagecho_blocks.json").read_text(encoding="utf-8"))["blocks"]
    block = blocks[BLOCK_INDEX]
    block_data = hagecho[block["start"] : block["end"]]
    module_payload_offset = payload_offset(block_data)
    if module_payload_offset != 0x10:
        raise ValueError("block 10 wrapper/payload offset changed")

    encoding = json.loads((project / "workspace/config/font_encoding.json").read_text(encoding="utf-8"))
    mapping = {int(item["sjis"], 16): item for item in encoding["mappings"]}

    actual_calls = [offset for offset in scan_calls(hagecho, GENERIC_STRING_BUILDER) if block["start"] <= offset < block["end"]]
    expected_calls = [item[0] for item in EXPECTED]
    if actual_calls != expected_calls:
        raise ValueError(f"block 10 call inventory changed: {actual_calls}")

    entries = []
    callsites = []
    for index, (call_offset, target_runtime, expected_text) in enumerate(EXPECTED, start=1):
        if hagecho[call_offset : call_offset + 4] != jal_word(GENERIC_STRING_BUILDER):
            raise ValueError(f"call word mismatch at 0x{call_offset:X}")
        high = (target_runtime >> 16) & 0xFFFF
        low = target_runtime & 0xFFFF
        expected_materialization = struct.pack("<II", 0x3C040000 | high, 0x24840000 | low)
        if hagecho[call_offset - 16 : call_offset - 8] != expected_materialization:
            raise ValueError(f"a0 target materialization changed at 0x{call_offset:X}")

        ara_offset = target_runtime - ARA_RUNTIME_BASE
        if not 0 <= ara_offset < len(ara):
            raise ValueError("ARA target outside file")
        decoded = decode_nul(ara, ara_offset, mapping, 0x40)
        if decoded["text"] != expected_text or decoded["unmapped_codes"]:
            raise ValueError(f"ARA literal changed at 0x{ara_offset:X}")
        entry_id = f"ara-literal-{index:02d}"
        entries.append({
            "entry_id": entry_id,
            "runtime_address": f"0x{target_runtime:08X}",
            "ara_offset": ara_offset,
            **decoded,
        })
        call_runtime = OVERLAY_BASE + call_offset - block["start"] - module_payload_offset
        callsites.append({
            "call_id": f"b010-call-{index:02d}",
            "file_offset": call_offset,
            "block_offset": call_offset - block["start"],
            "payload_offset": call_offset - block["start"] - module_payload_offset,
            "runtime_address": f"0x{call_runtime:08X}",
            "source_kind": "direct_ARA_literal",
            "source_target": entry_id,
            "status": "resolved_static_source",
        })

    report = {
        "schema_version": 1,
        "observation_id": "TEXT-CALLSITES-B010-001",
        "status": "BLOCK10_CALLSITES_FULLY_RESOLVED",
        "scope": {
            "revision_bin_sha256": "99dc6c7f748c4882d6f197a0a0a2eb943affc25bb4271deff9bb9bf55859aa54",
            "hagecho_sha256": sha256(hagecho),
            "ara_sha256": sha256(ara),
            "block_index": BLOCK_INDEX,
            "block_start": block["start"],
            "block_size": block["size"],
        },
        "module_layout": {
            "wrapper_header_size": module_payload_offset,
            "wrapper_header_hex": block_data[:module_payload_offset].hex().upper(),
            "runtime_base": f"0x{OVERLAY_BASE:08X}",
            "mapping": "file_offset = block_start + 0x10 + (runtime_address - 0x8012BCE0)",
        },
        "ara_layout": {
            "runtime_base": f"0x{ARA_RUNTIME_BASE:08X}",
            "mapping": "ARA offset = runtime_address - 0x7FFD6000",
            "anchor": "decoder 0x800430DC maps to ARA offset 0x6D0DC",
        },
        "denominator": {
            "generic_calls": len(callsites),
            "classified": len(callsites),
            "resolved_static_source": len(callsites),
            "unknown": 0,
        },
        "static_population": {
            "entry_count": len(entries),
            "entries": entries,
        },
        "callsites": callsites,
        "roundtrip": {
            "all_raw_codes_preserved": "PASS",
            "all_expected_call_and_a0_words_present": "PASS",
        },
        "next": "Classify the 19 calls located in ARA.BIN.",
    }
    output = project / "work/analysis/text_callsites_block10_001.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "denominator": report["denominator"],
        "entries": [{"ara_offset": item["ara_offset"], "text": item["text"]} for item in entries],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
