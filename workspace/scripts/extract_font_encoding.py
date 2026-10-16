#!/usr/bin/env python3
"""Extract the game's Shift-JIS-to-font-cell decoder mapping from runtime code."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


RAM_BASE = 0x80000000
DECODER_ADDRESS = 0x800430DC
SPECIAL_TABLE_ADDRESS = 0x800310A8
SPECIAL_FIRST = 0x8141
SPECIAL_COUNT = 0xB4
INVALID_CELL = 0xFF


def mapped_cell(code: int, special_cells: list[int]) -> int:
    if 0x824F <= code < 0x8259:
        return code - 0x824F
    if 0x8260 <= code < 0x827A:
        return code - 0x824F
    if 0x8281 <= code < 0x829B:
        return code - 0x8250
    if 0x829F <= code < 0x82F2:
        return code - 0x824F
    if 0x8340 <= code < 0x837F:
        return code - 0x829D
    if 0x8380 <= code < 0x8397:
        return code - 0x829E
    if SPECIAL_FIRST <= code < SPECIAL_FIRST + SPECIAL_COUNT:
        return special_cells[code - SPECIAL_FIRST]
    return INVALID_CELL


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--ram", type=Path, default=None)
    args = parser.parse_args()
    project = args.project.resolve()
    ram_path = args.ram or (project / "work/runtime/ui_ram_session_001/ram_title.bin")
    ram = ram_path.read_bytes()
    ara = (project / "extracted/ARA.BIN").read_bytes()

    decoder_file_offset = 0x6D0DC
    decoder_sample_size = 0x1BC
    ram_decoder = ram[DECODER_ADDRESS - RAM_BASE : DECODER_ADDRESS - RAM_BASE + decoder_sample_size]
    ara_decoder = ara[decoder_file_offset : decoder_file_offset + decoder_sample_size]
    if ram_decoder != ara_decoder:
        raise ValueError("runtime decoder does not match ARA.BIN at the established load mapping")

    table_offset = SPECIAL_TABLE_ADDRESS - RAM_BASE
    handlers = struct.unpack_from(f"<{SPECIAL_COUNT}I", ram, table_offset)
    special_cells = []
    handler_records = []
    for index, address in enumerate(handlers):
        instruction = struct.unpack_from("<I", ram, address - RAM_BASE + 4)[0]
        opcode = instruction >> 26
        rs = (instruction >> 21) & 0x1F
        rt = (instruction >> 16) & 0x1F
        immediate = instruction & 0xFFFF
        if not (opcode == 0x09 and rs == 0 and rt == 2):
            raise ValueError(f"unexpected special handler at 0x{address:08X}: 0x{instruction:08X}")
        cell = immediate
        special_cells.append(cell)
        handler_records.append(
            {
                "sjis": f"0x{SPECIAL_FIRST + index:04X}",
                "handler": f"0x{address:08X}",
                "cell": f"0x{cell:02X}",
            }
        )

    mappings = []
    cells: dict[int, list[int]] = {}
    for code in range(0x10000):
        cell = mapped_cell(code, special_cells)
        if cell == INVALID_CELL:
            continue
        raw = bytes((code >> 8, code & 0xFF))
        try:
            char = raw.decode("cp932")
        except UnicodeDecodeError:
            char = ""
        mappings.append({"sjis": f"{code:04X}", "bytes_hex": raw.hex(" ").upper(), "character": char, "cell": cell, "cell_hex": f"{cell:02X}"})
        cells.setdefault(cell, []).append(code)

    missing_cells = [cell for cell in range(0xFF) if cell not in cells]
    duplicate_cells = {f"{cell:02X}": [f"{code:04X}" for code in codes] for cell, codes in cells.items() if len(codes) != 1}
    sample_code = 0x8358
    report = {
        "schema_version": 1,
        "status": "RUNTIME_DECODER_EXTRACTED_WITH_ALIASES",
        "source_encoding": "Shift-JIS / CP932 two-byte code, big-endian byte order in strings",
        "terminator": "00 00 (loop tests the next lead byte for zero)",
        "decoder": {
            "runtime_address": f"0x{DECODER_ADDRESS:08X}",
            "ara_file_offset": f"0x{decoder_file_offset:X}",
            "sample_size": decoder_sample_size,
            "sha256": hashlib.sha256(ram_decoder).hexdigest(),
        },
        "special_jump_table": {
            "runtime_address": f"0x{SPECIAL_TABLE_ADDRESS:08X}",
            "first_sjis": f"0x{SPECIAL_FIRST:04X}",
            "entry_count": SPECIAL_COUNT,
            "valid_handler_count": sum(cell != INVALID_CELL for cell in special_cells),
            "entries": handler_records,
        },
        "mapping_count": len(mappings),
        "mapped_cell_range": "0x00-0xFE",
        "invalid_return": "0xFF",
        "missing_cells": [f"{cell:02X}" for cell in missing_cells],
        "duplicate_cells": duplicate_cells,
        "mapping_note": "The decoder is authoritative even where multiple Shift-JIS codes alias one cell or a populated atlas cell is unreachable through this decoder.",
        "runtime_sample": {
            "source_bytes": "83 58",
            "character": "ス",
            "expected_cell": f"0x{mapped_cell(sample_code, special_cells):02X}",
            "observed_cell": "0xBB",
            "writer": "0x80042F94 sb v0,0x0C(t0), with s1 holding decoder output",
        },
        "mappings": mappings,
    }
    output = project / "workspace/config/font_encoding.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "mapping_count": len(mappings), "sample": report["runtime_sample"]}, ensure_ascii=False))
    print(output)


if __name__ == "__main__":
    main()
