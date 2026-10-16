#!/usr/bin/env python3
"""Validate and record the Shift-JIS reader -> cell decoder -> POLY_FT4 chain."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

from capstone import CS_ARCH_MIPS, CS_MODE_LITTLE_ENDIAN, CS_MODE_MIPS32, Cs


RAM_BASE = 0x80000000
ARA_RUNTIME_DELTA = 0x7FFD6000
HAGECHO_RUNTIME_BASE = 0x8012BCE0


def one_instruction(ram: bytes, address: int) -> str:
    decoder = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 + CS_MODE_LITTLE_ENDIAN)
    raw = ram[address - RAM_BASE : address - RAM_BASE + 4]
    instruction = next(decoder.disasm(raw, address))
    return f"{instruction.mnemonic} {instruction.op_str}".strip()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    project = args.project.resolve()
    ram_path = project / "work/runtime/ui_ram_session_001/ram_title.bin"
    ram = ram_path.read_bytes()
    ara = (project / "extracted/ARA.BIN").read_bytes()
    hagecho = (project / "extracted/HAGECHO.BIN").read_bytes()
    trace_path = project / "work/analysis/font_write_trace_003.json"
    trace = json.loads(trace_path.read_text(encoding="utf-8"))

    ara_runtime = 0x80042E00
    ara_offset = ara_runtime - ARA_RUNTIME_DELTA
    if ram[ara_runtime - RAM_BASE : ara_runtime - RAM_BASE + 0x400] != ara[ara_offset : ara_offset + 0x400]:
        raise ValueError("ARA runtime mapping mismatch")
    hagecho_runtime = 0x80144C00
    hagecho_offset = hagecho_runtime - HAGECHO_RUNTIME_BASE
    if ram[hagecho_runtime - RAM_BASE : hagecho_runtime - RAM_BASE + 0x400] != hagecho[hagecho_offset : hagecho_offset + 0x400]:
        raise ValueError("HAGECHO runtime mapping mismatch")

    expected_instructions = {
        0x80042F40: "move $s1, $a0",
        0x80042F94: "sb $v0, 0xc($t0)",
        0x80042F98: "andi $v0, $s1, 0xf0",
        0x80042F9C: "sb $v0, 0xd($t0)",
        0x80144D44: "lbu $a0, ($s0)",
        0x80144D50: "lbu $v0, 1($s0)",
        0x80144D54: "jal 0x800430dc",
        0x80144D7C: "jal 0x80042f28",
        0x80144DF8: "addiu $s0, $s0, 2",
        0x80144E04: "lbu $v0, ($s0)",
    }
    actual_instructions = {address: one_instruction(ram, address) for address in expected_instructions}
    for address, expected in expected_instructions.items():
        if actual_instructions[address] != expected:
            raise ValueError(f"instruction mismatch at 0x{address:08X}: expected {expected}, got {actual_instructions[address]}")

    writer_hits = [hit for hit in trace["hits"] if hit["pc"] == "0x80042F98"]
    transform_hits = [hit for hit in trace["hits"] if hit["pc"] == "0x80144DDC"]
    if len(writer_hits) != 6 or len(transform_hits) != 6:
        raise ValueError("unexpected write-trace hit distribution")
    sample_writer = writer_hits[0]
    sample_transform = transform_hits[0]
    decoder_output = int(sample_writer["registers"]["gpr"][17], 16)
    string_pointer = int(sample_transform["registers"]["gpr"][16], 16)
    string_offset = string_pointer - HAGECHO_RUNTIME_BASE
    source_code = struct.unpack_from(">H", hagecho, string_offset)[0]
    if decoder_output != 0xBB or source_code != 0x8358:
        raise ValueError(f"runtime sample changed: source=0x{source_code:04X}, cell=0x{decoder_output:02X}")

    static_string_offset = 0x1A064
    static_string = bytes.fromhex("83 76 83 62 83 56 83 85 83 58 83 5E 81 5B 83 67 83 7B 83 5E 83 93")
    if hagecho[static_string_offset : static_string_offset + len(static_string)] != static_string:
        raise ValueError("known title prompt string changed")

    report = {
        "schema_version": 1,
        "observation_id": "FONT-CODE-001",
        "status": "PASS",
        "storage": {
            "format": "NUL-terminated two-byte Shift-JIS / CP932",
            "read_order": "lead byte then trail byte, combined as (lead << 8) | trail",
            "loop_step_bytes": 2,
            "terminator_test": "next lead byte == 0",
        },
        "runtime_chain": [
            {"stage": "string reader", "runtime_address": "0x80144D44", "hagecho_file_offset": "0x19064"},
            {"stage": "Shift-JIS to cell decoder", "runtime_address": "0x800430DC", "ara_file_offset": "0x6D0DC"},
            {"stage": "POLY_FT4 font primitive builder", "runtime_address": "0x80042F28", "ara_file_offset": "0x6CF28"},
            {"stage": "U coordinate from low nibble", "runtime_address": "0x80042F8C-0x80042F94", "rule": "u = (cell & 0x0F) << 4"},
            {"stage": "V coordinate from high nibble", "runtime_address": "0x80042F98-0x80042F9C", "rule": "v = cell & 0xF0"},
        ],
        "load_mappings": {
            "ara": {"runtime_sample": "0x80042E00", "file_offset": "0x6CE00", "formula": "runtime = file_offset + 0x7FFD6000"},
            "hagecho_block0": {"runtime_sample": "0x80144C00", "file_offset": "0x18F20", "formula": "runtime = file_offset + 0x8012BCE0"},
        },
        "runtime_sample": {
            "string_pointer_at_hit": f"0x{string_pointer:08X}",
            "hagecho_file_offset": f"0x{string_offset:X}",
            "source_bytes": "83 58",
            "source_character": "ス",
            "decoder_cell": "0xBB",
            "observed_u_write": "0xB0",
            "observed_v_write": "0xB0",
            "source_string": "プッシュスタートボタン",
            "source_string_hagecho_offset": f"0x{static_string_offset:X}",
        },
        "trace": {
            "source": trace_path.relative_to(project).as_posix(),
            "sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(),
            "writer_hits": len(writer_hits),
            "post_transform_hits": len(transform_hits),
            "post_transform_note": "0x80144DDC reorders UV vertices after the cell has already been selected; it is not the character decoder.",
        },
        "discarded": {
            "font_write_trace_002": "0x80046894 wrote a different menu quad into reused primitive memory; it did not carry the font cell code and is not the font writer."
        },
        "verified_instructions": {f"0x{address:08X}": text for address, text in actual_instructions.items()},
    }
    output = project / "work/analysis/font_writer_analysis.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "runtime_sample": report["runtime_sample"]}, ensure_ascii=False))
    print(output)


if __name__ == "__main__":
    main()
