#!/usr/bin/env python3
"""Validate the title text motion controller and its link to the font builder."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

from capstone import CS_ARCH_MIPS, CS_MODE_LITTLE_ENDIAN, CS_MODE_MIPS32, Cs


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

RAM_BASE = 0x80000000
HAGECHO_RUNTIME_BASE = 0x8012BCE0


def instruction_map(ram: bytes, start: int, end: int) -> dict[int, str]:
    decoder = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 + CS_MODE_LITTLE_ENDIAN)
    raw = ram[start - RAM_BASE : end - RAM_BASE]
    return {item.address: f"{item.mnemonic} {item.op_str}".strip() for item in decoder.disasm(raw, start)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    project = args.project.resolve()
    ram_path = project / "work/runtime/ui_ram_session_001/ram_title.bin"
    video_report_path = project / "work/analysis/font_motion_001_video.json"
    ram = ram_path.read_bytes()
    hagecho = (project / "extracted/HAGECHO.BIN").read_bytes()
    video_report = json.loads(video_report_path.read_text(encoding="utf-8"))

    text_builder = 0x80144CFC
    motion_controller = 0x80144E38
    controller_end = 0x80144FB4
    mapping_start = 0x80144CFC
    mapping_end = 0x80144FB4
    hagecho_start = mapping_start - HAGECHO_RUNTIME_BASE
    hagecho_end = mapping_end - HAGECHO_RUNTIME_BASE
    if ram[mapping_start - RAM_BASE : mapping_end - RAM_BASE] != hagecho[hagecho_start:hagecho_end]:
        raise ValueError("title text code no longer matches HAGECHO mapping")

    ins = instruction_map(ram, mapping_start, mapping_end)
    expected = {
        0x80144D7C: "jal 0x80042f28",
        0x80144DFC: "sll $v0, $s3, 4",
        0x80144E00: "addu $s1, $s1, $v0",
        0x80144E9C: "lw $v0, 0x5cc8($v0)",
        0x80144EA4: "mult $v0, $v0",
        0x80144EC0: "subu $v0, $v0, $v1",
        0x80144EEC: "addu $v1, $v1, $v0",
        0x80144F1C: "jal 0x80144cfc",
        0x80144F30: "jal 0x80144cfc",
        0x80144F48: "jal 0x80144cfc",
        0x80144F68: "jal 0x80144cfc",
        0x80144F7C: "jal 0x80144cfc",
        0x80144F94: "jal 0x80144cfc",
    }
    for address, text in expected.items():
        if ins.get(address) != text:
            raise ValueError(f"instruction mismatch at 0x{address:08X}: {ins.get(address)!r}")

    call_word = struct.pack("<I", 0x0C000000 | ((text_builder >> 2) & 0x03FFFFFF))
    call_addresses = [
        RAM_BASE + offset
        for offset in range(0, len(ram) - 3, 4)
        if ram[offset : offset + 4] == call_word
    ]
    expected_calls = [0x80144F1C, 0x80144F30, 0x80144F48, 0x80144F68, 0x80144F7C, 0x80144F94]
    if call_addresses != expected_calls:
        raise ValueError(f"unexpected callers of title string builder: {call_addresses}")

    report = {
        "schema_version": 1,
        "observation_id": "FONT-MOTION-001",
        "status": "IMPLEMENTATION_REUSE_CONFIRMED",
        "video": {
            "source_sha256": video_report["source"]["sha256"],
            "duration_seconds": video_report["video"]["duration_seconds"],
            "fps": video_report["video"]["fps"],
            "sample_count": len(video_report["samples"]),
            "contact_sheet": video_report["contact_sheet"],
            "observation": "The center scene remains fixed while the left and right prompt fragments occupy different vertical positions across sampled frames.",
        },
        "code": {
            "string_builder": {
                "runtime": f"0x{text_builder:08X}",
                "hagecho_offset": f"0x{text_builder - HAGECHO_RUNTIME_BASE:X}",
                "behavior": "decode each two-byte character, build one 16x16 POLY_FT4, advance the selected axis by 16 pixels, and optionally reorder UV vertices for the opposite edge orientation",
            },
            "motion_controller": {
                "runtime": f"0x{motion_controller:08X}",
                "hagecho_offset": f"0x{motion_controller - HAGECHO_RUNTIME_BASE:X}",
                "scroll_state": ["0x80145CC0", "0x80145CC4"],
                "bounce_counter": "0x80145CC8",
                "bounce_rule": "counter squared before it is applied to the two edge base positions",
                "wrap_spacing_pixels": 256,
                "builder_call_count_per_update": 6,
                "instances": "three copies at scroll-256, scroll, and scroll+256 for each of two edge orientations",
            },
            "verified_instructions": {f"0x{address:08X}": text for address, text in expected.items()},
            "ram_sha256": hashlib.sha256(ram).hexdigest(),
        },
        "assessment": {
            "separate_hangul_motion_implementation_required": False,
            "reason": "The controller changes POLY_FT4 positions after character decoding and is independent of the glyph bitmap. A Hangul cell emitted through the existing builder inherits the same motion automatically.",
            "preserve": [
                "one decoded character must still produce one 16x16 POLY_FT4",
                "the title path must keep the 16-pixel character advance and both orientation modes",
                "the translated border prompt must be checked against the 256-pixel repetition interval",
            ],
            "not_proven": "Other screens may use different motion controllers or static render paths and require path-specific verification.",
        },
    }
    output = project / "work/analysis/font_motion_001_code.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "assessment": report["assessment"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
