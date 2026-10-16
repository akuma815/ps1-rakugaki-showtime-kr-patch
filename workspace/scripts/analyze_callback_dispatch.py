#!/usr/bin/env python3
"""Validate the low-frequency callback dispatcher used as a runtime probe.

The 2026-08-09 handoff called 0x8008C8A8 an asset-directory walker.  Static
code says something narrower and more useful: it queues or immediately invokes
a callback, preserving a small payload.  This script pins that ABI to the
supported ARA.BIN revision before the runtime tracer relies on it.
"""

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

ARA_RUNTIME_BASE = 0x7FFD6000
DISPATCH_ENTRY = 0x8008C8A8
DISPATCH_FILE_OFFSET = DISPATCH_ENTRY - ARA_RUNTIME_BASE
WRAPPER_ENTRY = 0x8008C884
EXPORT_POINTER_OFFSET = 0xD3564


def instruction_map(data: bytes, start: int, end: int) -> dict[int, str]:
    decoder = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 + CS_MODE_LITTLE_ENDIAN)
    raw = data[start - ARA_RUNTIME_BASE : end - ARA_RUNTIME_BASE]
    return {item.address: f"{item.mnemonic} {item.op_str}".strip() for item in decoder.disasm(raw, start)}


def analyze(ara: bytes, expected_sha256: str) -> dict:
    actual_sha256 = hashlib.sha256(ara).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError(f"ARA.BIN SHA-256 mismatch: {actual_sha256}")
    if struct.unpack_from("<I", ara, EXPORT_POINTER_OFFSET)[0] != DISPATCH_ENTRY:
        raise ValueError("dispatcher export pointer mismatch")

    ins = instruction_map(ara, WRAPPER_ENTRY, 0x8008CB58)
    expected = {
        0x8008C88C: "move $a3, $a2",
        0x8008C890: "jal 0x8008c8a8",
        0x8008C894: "move $a2, $zero",
        0x8008C8B0: "move $s3, $a0",
        0x8008C8B8: "move $s0, $a1",
        0x8008C8C0: "move $s1, $a2",
        0x8008C8D0: "move $s2, $a3",
        0x8008C9A8: "move $a0, $s0",
        0x8008C9AC: "jalr $s3",
        0x8008C9B0: "move $a1, $s2",
        0x8008CA10: "lw $a1, ($a3)",
        0x8008CA14: "addiu $a3, $a3, 4",
        0x8008CAF0: "sw $s3, -0x6e18($at)",
    }
    for address, text in expected.items():
        if ins.get(address) != text:
            raise ValueError(f"instruction mismatch at 0x{address:08X}: {ins.get(address)!r}")

    direct_call = struct.pack("<I", 0x0C000000 | ((DISPATCH_ENTRY >> 2) & 0x03FFFFFF))
    direct_callers = [
        ARA_RUNTIME_BASE + offset
        for offset in range(0, len(ara) - 3, 4)
        if ara[offset : offset + 4] == direct_call
    ]
    if direct_callers != [0x8008C890]:
        raise ValueError(f"unexpected direct callers: {direct_callers}")

    return {
        "schema_version": 1,
        "observation_id": "CALLBACK-DISPATCH-STATIC-001",
        "status": "ABI_CONFIRMED_RUNTIME_IDENTITY_PENDING",
        "source": {
            "path": "extracted/ARA.BIN",
            "sha256": actual_sha256,
            "runtime_base": f"0x{ARA_RUNTIME_BASE:08X}",
        },
        "dispatcher": {
            "runtime": f"0x{DISPATCH_ENTRY:08X}",
            "file_offset": f"0x{DISPATCH_FILE_OFFSET:X}",
            "export_pointer_offset": f"0x{EXPORT_POINTER_OFFSET:X}",
            "abi": {
                "a0": "callback function pointer",
                "a1": "payload pointer passed back to callback as a0",
                "a2": "payload byte count copied into the queue; zero keeps the pointer",
                "a3": "callback argument passed back as a1",
            },
            "immediate_callback": "callback(a1_payload_pointer, a3_callback_argument)",
            "direct_callers": [f"0x{address:08X}" for address in direct_callers],
            "verified_instructions": {f"0x{address:08X}": text for address, text in expected.items()},
        },
        "assessment": {
            "confirmed": "0x8008C8A8 is a callback queue/dispatcher; its runtime call frequency must be measured before using it as a navigation-time breakpoint.",
            "rejected": "The entry itself is an asset-directory walker or receives a directory pointer in a0.",
            "unknown": "Which callback and payload correspond to the character-select チーム/カラー assets.",
            "next": "Trace calls while entering character select, then compare callback and payload snapshots across the transition.",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    project = args.project.resolve()
    manifest = json.loads((project / "workspace/config/source_manifest.json").read_text(encoding="utf-8"))
    ara_path = project / "extracted/ARA.BIN"
    report = analyze(ara_path.read_bytes(), manifest["expected_files"]["ARA.BIN"]["sha256"])
    output = project / "work/analysis/callback_dispatch_static_001.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": output.relative_to(project).as_posix()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
