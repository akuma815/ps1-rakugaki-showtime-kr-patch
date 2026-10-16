#!/usr/bin/env python3
"""Trace the bank-1 decoder result through the patched primitive builder."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from capture_duckstation_ram import connect_with_retry


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


EXPECTED_SETTINGS_SHA256 = "02037cc124559e370bdf7dea165806c272ee97b835fcd61bfa732567219e9c9d"
BREAK_ADDRESS = 0x80042F84


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def registers(packet: bytes) -> list[int]:
    raw = bytes.fromhex(packet.decode("ascii"))
    return [struct.unpack_from("<I", raw, index * 4)[0] for index in range(min(len(raw) // 4, 38))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--duckstation", type=Path, default=Path(r"H:\Duckstation"))
    parser.add_argument("--delay", type=float, default=35.0)
    parser.add_argument("--max-hits", type=int, default=128)
    parser.add_argument("--port", type=int, default=2345)
    args = parser.parse_args()
    project = args.project.resolve()
    duckstation = args.duckstation.resolve()
    exe = duckstation / "duckstation-qt-x64-ReleaseLTCG.exe"
    settings = duckstation / "settings.ini"
    build = json.loads((project / "work/analysis/font_two_bank_poc_001_build.json").read_text(encoding="utf-8"))
    cue = project / build["output"]["cue"]
    disc_bin = project / build["output"]["bin"]
    report_path = project / "work/analysis/font_two_bank_poc_001_trace.json"
    if report_path.exists():
        raise FileExistsError(f"refusing to overwrite existing trace: {report_path}")
    if sha256(settings) != EXPECTED_SETTINGS_SHA256:
        raise ValueError("DuckStation settings baseline mismatch")
    if sha256(cue) != build["output"]["cue_sha256"] or sha256(disc_bin) != build["output"]["bin_sha256"]:
        raise ValueError("two-bank build hash mismatch")

    command = [str(exe), "-batch", "-nogui", "-fastboot", "--", str(cue)]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(command, cwd=duckstation, creationflags=creationflags)
    client = None
    installed = False
    hit_records: list[dict] = []
    selected: dict | None = None
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        client = connect_with_retry(args.port, 20)
        client.send_packet("qSupported:multiprocess+;swbreak+;hwbreak+")
        client.send_packet("?")
        client.resume()
        time.sleep(args.delay)
        client.interrupt()
        reply = client.send_packet(f"Z0,{BREAK_ADDRESS:x},4") or b""
        if reply != b"OK":
            raise RuntimeError(f"breakpoint install failed: {reply!r}")
        installed = True
        client.resume()
        for index in range(args.max_hits):
            stop = client.recv_packet()
            packet = client.send_packet("g") or b""
            values = registers(packet)
            if len(values) < 38:
                raise ValueError("short register packet")
            pc, s1, t0 = values[37], values[17], values[8]
            record = {
                "index": index,
                "stop_reply": stop.decode("ascii", errors="replace"),
                "pc": f"0x{pc:08X}",
                "s1_decoder_value": f"0x{s1:08X}",
                "t0_primitive": f"0x{t0:08X}",
            }
            hit_records.append(record)
            if 0x100 <= s1 <= 0x1FF:
                client.send_packet(f"z0,{BREAK_ADDRESS:x},4")
                installed = False
                before = client.read_memory(t0 + 0x0C, 12, chunk_size=12)
                steps = []
                for _ in range(5):
                    client.send_packet("s", expect_reply=False)
                    step_stop = client.recv_packet()
                    step_values = registers(client.send_packet("g") or b"")
                    steps.append({"stop_reply": step_stop.decode("ascii", errors="replace"), "pc": f"0x{step_values[37]:08X}"})
                after = client.read_memory(t0 + 0x0C, 12, chunk_size=12)
                selected = {
                    **record,
                    "bank": s1 >> 8,
                    "cell": s1 & 0xFF,
                    "uv_tpage_bytes_before": before.hex(" ").upper(),
                    "uv_tpage_bytes_after": after.hex(" ").upper(),
                    "u": after[0],
                    "v": after[1],
                    "tpage": int.from_bytes(after[10:12], "little"),
                    "steps": steps,
                }
                break
            client.resume()
    finally:
        if client is not None:
            if installed:
                try:
                    client.send_packet(f"z0,{BREAK_ADDRESS:x},4")
                except Exception:
                    pass
            client.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    status = "PASS" if selected and selected["tpage"] == 0x1E and selected["u"] == 0xE0 and selected["v"] == 0xD0 else "BANK1_RESULT_NOT_CONFIRMED"
    report = {
        "schema_version": 1,
        "observation_id": "FONT-TWO-BANK-POC-001",
        "status": status,
        "started_at": started_at,
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "baseline": {
            "bin_sha256": build["output"]["bin_sha256"],
            "cue_sha256": build["output"]["cue_sha256"],
            "settings_sha256": EXPECTED_SETTINGS_SHA256,
        },
        "breakpoint": f"0x{BREAK_ADDRESS:08X}",
        "hit_count": len(hit_records),
        "selected_bank1_hit": selected,
        "expected": {"decoder_values": ["0x1DE", "0x1DF"], "tpage": "0x001E", "cells": ["0xDE", "0xDF"]},
        "interpretation": "PASS proves the runtime decoder bank bit reaches the shared builder and changes tpage while preserving the low-byte UV mapping; it does not prove final bank-1 VRAM persistence.",
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if sha256(settings) != EXPECTED_SETTINGS_SHA256:
        raise ValueError("DuckStation settings changed during trace")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
