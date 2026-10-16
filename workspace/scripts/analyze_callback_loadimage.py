#!/usr/bin/env python3
"""Extract and locate LoadImage calls captured by trace_callback_dispatch.py."""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

LOADIMAGE_ENTRY = "0x8008C2D8"


def find_all(blob: bytes, needle: bytes, limit: int = 32) -> list[int]:
    hits = []
    cursor = 0
    while needle and len(hits) < limit:
        offset = blob.find(needle, cursor)
        if offset < 0:
            break
        hits.append(offset)
        cursor = offset + 1
    return hits


def extract(trace: dict, blobs: dict[str, bytes]) -> tuple[list[dict], list[dict]]:
    uploads = []
    located = []
    for call in trace["calls"]:
        if call["callback"]["value"] != LOADIMAGE_ENTRY:
            continue
        rect_raw = bytes.fromhex(call["payload"].get("hex", ""))
        if len(rect_raw) < 8:
            raise ValueError(f"LoadImage call {call['index']} has no RECT snapshot")
        rect = list(struct.unpack_from("<4h", rect_raw))
        if rect[2] <= 0 or rect[3] <= 0:
            raise ValueError(f"LoadImage call {call['index']} has invalid RECT {rect}")
        signature = bytes.fromhex(call["callback_argument"].get("hex", ""))
        if not signature:
            raise ValueError(f"LoadImage call {call['index']} has no source signature")
        hits = [
            {"file": name, "offset": f"0x{offset:X}"}
            for name, blob in blobs.items()
            for offset in find_all(blob, signature)
        ]
        record = {
            "call_index": call["index"],
            "elapsed_s": call["elapsed_s"],
            "source": call["callback_argument"]["value"],
            "rect": rect,
            "signature": signature.hex().upper(),
            "location_hits": hits,
        }
        uploads.append(record)
        if len(hits) == 1:
            located.append({**hits[0], "rect": rect, "source": record["source"],
                            "elapsed_s": record["elapsed_s"], "call_index": record["call_index"]})
    return uploads, located


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--trace", default="work/analysis/callback_dispatch_trace_charselect_gate_003.json")
    parser.add_argument("--session", default="charselect_gate_003")
    args = parser.parse_args()
    project = args.project.resolve()
    trace = json.loads((project / args.trace).read_text(encoding="utf-8"))
    blobs = {
        "ARA": (project / "extracted/ARA.BIN").read_bytes(),
        "HAGECHO": (project / "extracted/HAGECHO.BIN").read_bytes(),
    }
    uploads, located = extract(trace, blobs)
    report = {
        "schema_version": 1,
        "observation_id": "CALLBACK-DISPATCH-LOADIMAGE-001",
        "status": "LOCATED_WITH_UNRESOLVED_PATCHED_OR_DYNAMIC_SOURCES" if len(located) < len(uploads) else "ALL_LOCATED",
        "source_trace": args.trace,
        "loadimage_calls": len(uploads),
        "unique_locations": len(located),
        "unresolved_calls": len(uploads) - len(located),
        "uploads": uploads,
    }
    report_path = project / "work/analysis" / f"callback_loadimage_{args.session}.json"
    located_path = project / "work/analysis" / f"callback_loadimage_{args.session}_located.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    located_path.write_text(json.dumps(located, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "loadimage_calls": len(uploads),
                      "unique_locations": len(located), "report": report_path.relative_to(project).as_posix(),
                      "located": located_path.relative_to(project).as_posix()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
