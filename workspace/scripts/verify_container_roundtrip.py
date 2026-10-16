#!/usr/bin/env python3
"""Verify that extracted HAGECHO blocks and DO offsets round-trip byte-identically."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    project = args.project.resolve()
    report = json.loads((project / "work/analysis/hagecho_blocks.json").read_text(encoding="utf-8"))
    original_hagecho = (project / "extracted/HAGECHO.BIN").read_bytes()
    original_do = (project / "extracted/DO.BIN").read_bytes()

    parts: list[bytes] = []
    rebuilt_offsets = [0]
    block_results = []
    for block in report["blocks"]:
        path = project / "extracted/hagecho_blocks" / f"block_{block['index']:03d}.bin"
        data = path.read_bytes()
        actual_hash = digest(data)
        if len(data) != block["size"] or actual_hash != block["sha256"]:
            raise ValueError(f"block {block['index']:03d} differs from inventory")
        parts.append(data)
        rebuilt_offsets.append(rebuilt_offsets[-1] + len(data))
        block_results.append({"index": block["index"], "size": len(data), "sha256": actual_hash, "match": True})

    rebuilt_hagecho = b"".join(parts)
    rebuilt_do = struct.pack(f"<{len(rebuilt_offsets)}I", *rebuilt_offsets)
    hagecho_match = rebuilt_hagecho == original_hagecho
    do_match = rebuilt_do == original_do
    if not hagecho_match or not do_match:
        raise ValueError(f"container round-trip failed: HAGECHO={hagecho_match}, DO={do_match}")

    result = {
        "schema_version": 1,
        "status": "PASS",
        "block_count": len(parts),
        "hagecho": {"byte_identical": True, "size": len(rebuilt_hagecho), "sha256": digest(rebuilt_hagecho)},
        "do": {"byte_identical": True, "size": len(rebuilt_do), "sha256": digest(rebuilt_do)},
        "blocks": block_results,
    }
    output = project / "work/analysis/container_roundtrip.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"status={result['status']}")
    print(f"block_count={result['block_count']}")
    print(f"HAGECHO_SHA256={result['hagecho']['sha256']}")
    print(f"DO_SHA256={result['do']['sha256']}")
    print(output)


if __name__ == "__main__":
    main()
