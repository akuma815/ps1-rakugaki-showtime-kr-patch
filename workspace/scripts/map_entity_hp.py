#!/usr/bin/env python3
"""Map the in-stage entity HP slots by sampling RAM while a human plays.

Known public GameShark codes place the player's HP halfword at
0x800D60FC/0x800D64FC/0x800D66FC/0x800D6BFC (0x091D max) depending on the
stage, suggesting an entity array near 0x800D6000. This session snapshots the
candidate region every --interval seconds while the user follows a scripted
sequence (idle -> take hits -> deal hits), then reports every halfword that
behaves like an HP value (starts near a stable max, decreases over time).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from build_two_bank_poc import sha256_file
from capture_duckstation_ram import RspClient, connect_with_retry
from capture_vram_occupancy import EXPECTED_SETTINGS_SHA256S, capture_window


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


REGION_BASE = 0x800D5000
REGION_SIZE = 0x3800
KNOWN_PLAYER_SLOTS = (0x800D60FC, 0x800D64FC, 0x800D66FC, 0x800D6BFC)
HP_MAX = 0x091D


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--duckstation", type=Path, default=Path(r"H:\Duckstation"))
    parser.add_argument("--session", default="001")
    parser.add_argument("--duration", type=float, default=300.0)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--port", type=int, default=2345)
    args = parser.parse_args()
    project = args.project.resolve()
    duckstation = args.duckstation.resolve()
    exe = duckstation / "duckstation-qt-x64-ReleaseLTCG.exe"
    settings = duckstation / "settings.ini"
    if sha256_file(settings) not in EXPECTED_SETTINGS_SHA256S:
        raise ValueError("DuckStation settings baseline mismatch")
    build = json.loads((project / "work/analysis/font_two_bank_poc_002_build.json").read_text(encoding="utf-8"))
    cue = project / build["output"]["cue"]
    if sha256_file(cue) != build["output"]["cue_sha256"]:
        raise ValueError("PoC cue hash mismatch")

    output_dir = project / "work/runtime" / f"hp_map_{args.session}"
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite: {output_dir}")
    output_dir.mkdir(parents=True)

    process = subprocess.Popen([str(exe), "-batch", "-fastboot", "--", str(cue)], cwd=duckstation)
    client = None
    samples = []
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        client = connect_with_retry(args.port, 20)
        client.send_packet("qSupported:multiprocess+;swbreak+;hwbreak+")
        client.send_packet("?")
        client.resume()
        start = time.monotonic()
        index = 0
        while time.monotonic() - start < args.duration:
            time.sleep(args.interval)
            try:
                client.interrupt()
                data = client.read_memory(REGION_BASE, REGION_SIZE)
                client.resume()
            except Exception as error:
                print(f"sample {index} failed: {error}")
                break
            path = output_dir / f"sample_{index:04d}.bin"
            path.write_bytes(data)
            record = {
                "index": index,
                "seconds": round(time.monotonic() - start, 2),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            if index % 5 == 0:
                shot = output_dir / f"sample_{index:04d}_screen.png"
                if capture_window(process.pid, shot):
                    record["screenshot"] = shot.name
            samples.append(record)
            index += 1
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

    # Analysis: halfwords that look like HP (bounded, mostly stable, decreasing).
    import struct

    series: dict[int, list[int]] = {}
    for record in samples:
        data = (output_dir / f"sample_{record['index']:04d}.bin").read_bytes()
        for offset in range(0, REGION_SIZE, 2):
            value = struct.unpack_from("<H", data, offset)[0]
            series.setdefault(offset, []).append(value)
    candidates = []
    for offset, values in series.items():
        nonzero = [v for v in values if v]
        if not nonzero or max(nonzero) > 0x2000:
            continue
        decreases = sum(1 for a, b in zip(values, values[1:]) if b < a and b != 0)
        increases = sum(1 for a, b in zip(values, values[1:]) if b > a and a != 0)
        if decreases >= 2 and max(nonzero) >= 0x80:
            address = REGION_BASE + offset
            candidates.append({
                "address": f"0x{address:08X}",
                "known_player_slot": address in KNOWN_PLAYER_SLOTS,
                "max": max(nonzero),
                "min_nonzero": min(nonzero),
                "decreases": decreases,
                "increases": increases,
                "first": values[0],
                "last": values[-1],
            })
    candidates.sort(key=lambda item: (-item["decreases"], item["address"]))
    report = {
        "schema_version": 1,
        "observation_id": "ENTITY-HP-MAP-001",
        "session": args.session,
        "status": "CAPTURED",
        "started_at": started_at,
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "region": {"base": f"0x{REGION_BASE:08X}", "size": REGION_SIZE},
        "sample_count": len(samples),
        "interval_seconds": args.interval,
        "known_player_slots": [f"0x{addr:08X}" for addr in KNOWN_PLAYER_SLOTS],
        "hp_max_reference": HP_MAX,
        "candidates": candidates,
        "samples": samples,
    }
    report_path = project / "work/analysis" / f"entity_hp_map_{args.session}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"samples": len(samples), "hp_candidates": len(candidates)}, indent=2))
    for item in candidates[:20]:
        print(item)


if __name__ == "__main__":
    main()
