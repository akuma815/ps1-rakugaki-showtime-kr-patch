#!/usr/bin/env python3
"""Sample the global counter the font builder uses to pick its CLUT palette.

The stock builder computes palette = (counter >> 2) & 3 from the word at
0x800AECF0. The Korean build uses only bit 2 of that counter, so if bit 2 never
toggles the two-frame wobble is frozen. This reports which bits actually move.
"""

from __future__ import annotations

import argparse
import json
import struct
import subprocess
import sys
import time
from pathlib import Path

from build_two_bank_poc import sha256_file
from capture_duckstation_ram import connect_with_retry


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

COUNTER_ADDRESS = 0x800AECF0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--duckstation", type=Path, default=Path(r"H:\Duckstation"))
    parser.add_argument("--settle", type=float, default=32.0)
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--interval", type=float, default=0.35)
    parser.add_argument("--port", type=int, default=2345)
    args = parser.parse_args()
    project = args.project.resolve()
    build = json.loads((project / "work/analysis/hangul_atlas_001_build.json").read_text(encoding="utf-8"))
    cue = project / build["output"]["cue"]
    if sha256_file(cue) != build["output"]["cue_sha256"]:
        raise ValueError("build cue hash mismatch")
    exe = args.duckstation.resolve() / "duckstation-qt-x64-ReleaseLTCG.exe"

    process = subprocess.Popen([str(exe), "-batch", "-fastboot", "--", str(cue)],
                               cwd=args.duckstation.resolve())
    values = []
    client = None
    try:
        client = connect_with_retry(args.port, 20)
        client.send_packet("qSupported:multiprocess+;swbreak+;hwbreak+")
        client.send_packet("?")
        client.resume()
        time.sleep(args.settle)
        for _ in range(args.samples):
            client.interrupt()
            raw = client.read_memory(COUNTER_ADDRESS, 4, chunk_size=4)
            client.resume()
            values.append(struct.unpack("<I", raw)[0])
            time.sleep(args.interval)
    finally:
        if client is not None:
            client.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    toggling = [bit for bit in range(16) if len({(v >> bit) & 1 for v in values}) > 1]
    report = {
        "schema_version": 1,
        "observation_id": "FONT-WOBBLE-COUNTER-001",
        "counter_address": f"0x{COUNTER_ADDRESS:08X}",
        "sample_count": len(values),
        "distinct_values": sorted({f"0x{v:08X}" for v in values}),
        "toggling_bits": toggling,
        "stock_palette_sequence": sorted({(v >> 2) & 3 for v in values}),
        "korean_build_palette_bit": 2,
        "korean_build_animation_states": sorted({(v >> 2) & 1 for v in values}),
        "samples": [f"0x{v:08X}" for v in values],
    }
    path = project / "work/analysis/font_wobble_counter_001.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in
                      ("distinct_values", "toggling_bits", "stock_palette_sequence", "korean_build_animation_states")},
                     indent=2))


if __name__ == "__main__":
    main()
