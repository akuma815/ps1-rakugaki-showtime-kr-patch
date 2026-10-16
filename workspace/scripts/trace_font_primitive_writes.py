#!/usr/bin/env python3
"""Use DuckStation GDB write breakpoints to locate the font-primitive writer."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import subprocess
from datetime import datetime
from pathlib import Path

from capture_duckstation_ram import connect_with_retry
from capture_ui_ram_stages import sha256, wait_for_new_screenshot


EXPECTED_SETTINGS_SHA256 = "02037cc124559e370bdf7dea165806c272ee97b835fcd61bfa732567219e9c9d"
EXPECTED_BIN_SHA256 = "99dc6c7f748c4882d6f197a0a0a2eb943affc25bb4271deff9bb9bf55859aa54"
EXPECTED_CUE_SHA256 = "4c6d97218e38fb592e9c426bf2fa086a2110005a82ab062352387594e0af3f97"

# UV fields (+0x0C) of the first observed POLY_FT4 in each double-buffered text group.
WATCH_ADDRESSES = [0x800B3100, 0x800B3B50, 0x800B3F38, 0x800C5374, 0x800C5DC4, 0x800C61AC]


def parse_registers(packet: bytes) -> dict[str, object]:
    raw = bytes.fromhex(packet.decode("ascii"))
    values = [struct.unpack_from("<I", raw, index * 4)[0] for index in range(min(len(raw) // 4, 38))]
    return {
        "gpr": [f"0x{value:08X}" for value in values[:32]],
        "sr": f"0x{values[32]:08X}",
        "lo": f"0x{values[33]:08X}",
        "hi": f"0x{values[34]:08X}",
        "badvaddr": f"0x{values[35]:08X}",
        "cause": f"0x{values[36]:08X}",
        "pc": f"0x{values[37]:08X}",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--duckstation", type=Path, default=Path(r"H:\Duckstation"))
    parser.add_argument("--session-id", default="font_write_trace_001")
    parser.add_argument("--max-hits", type=int, default=24)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--port", type=int, default=2345)
    args = parser.parse_args()

    project = args.project.resolve()
    duckstation = args.duckstation.resolve()
    exe = duckstation / "duckstation-qt-x64-ReleaseLTCG.exe"
    cue = duckstation / "Roms/Rakugaki Showtime (J)/Rakugaki Showtime (Japan).cue"
    disc_bin = cue.with_suffix(".bin")
    settings = duckstation / "settings.ini"
    screenshots_root = duckstation / "screenshots"
    output_root = project / "work/runtime" / args.session_id
    report_path = project / "work/analysis" / f"{args.session_id}.json"
    if output_root.exists() or report_path.exists():
        raise FileExistsError(f"refusing to overwrite existing session: {args.session_id}")
    actual_hashes = {"settings": sha256(settings), "disc_bin": sha256(disc_bin), "disc_cue": sha256(cue)}
    expected_hashes = {"settings": EXPECTED_SETTINGS_SHA256, "disc_bin": EXPECTED_BIN_SHA256, "disc_cue": EXPECTED_CUE_SHA256}
    if actual_hashes != expected_hashes:
        raise ValueError(f"baseline hash mismatch: expected={expected_hashes}, actual={actual_hashes}")

    baseline_screenshots = {path.resolve() for path in screenshots_root.glob("*.png")}
    output_root.mkdir(parents=True, exist_ok=False)
    process: subprocess.Popen[bytes] | None = None
    client = None
    hits = []
    breakpoint_replies = {}
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        process = subprocess.Popen([str(exe), "-batch", "-fastboot", "--", str(cue)], cwd=duckstation)
        client = connect_with_retry(args.port, 20)
        client.socket.settimeout(120)
        supported = client.send_packet("qSupported:multiprocess+;swbreak+;hwbreak+")
        initial_status = client.send_packet("?")
        client.resume()
        print("Mode Select에서 Options를 선택한 상태로 F10을 누르고 2초 뒤 결정 버튼을 누르세요.", flush=True)
        screenshot = wait_for_new_screenshot(screenshots_root, baseline_screenshots, set(), process, args.timeout)
        client.interrupt()
        before = {address: client.read_memory(address, 4, chunk_size=4) for address in WATCH_ADDRESSES}
        for address in WATCH_ADDRESSES:
            reply = client.send_packet(f"Z2,{address:x}") or b""
            breakpoint_replies[f"0x{address:08X}"] = reply.decode("ascii", errors="replace")
        screenshot_target = output_root / screenshot.name
        shutil.move(str(screenshot), str(screenshot_target))
        print("감시점 설치 완료: 이제 결정 버튼을 누르세요.", flush=True)
        client.resume()
        for index in range(args.max_hits):
            stop_reply = client.recv_packet()
            register_packet = client.send_packet("g") or b""
            registers = parse_registers(register_packet)
            pc = int(str(registers["pc"]), 16)
            code_base = max(0, pc - 16)
            code = client.read_memory(code_base, 48)
            current = {address: client.read_memory(address, 4, chunk_size=4) for address in WATCH_ADDRESSES}
            changed = [
                {
                    "address": f"0x{address:08X}",
                    "before_hex": before[address].hex(" ").upper(),
                    "after_hex": current[address].hex(" ").upper(),
                }
                for address in WATCH_ADDRESSES
                if current[address] != before[address]
            ]
            hits.append(
                {
                    "index": index,
                    "stop_reply": stop_reply.decode("ascii", errors="replace"),
                    "pc": registers["pc"],
                    "registers": registers,
                    "code_window_address": f"0x{code_base:08X}",
                    "code_window_hex": code.hex(" ").upper(),
                    "changed_watch_values": changed,
                }
            )
            before = current
            if index + 1 < args.max_hits:
                client.resume()
        for address in WATCH_ADDRESSES:
            client.send_packet(f"z2,{address:x}")
    finally:
        if client is not None:
            client.close()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    if sha256(settings) != EXPECTED_SETTINGS_SHA256:
        raise ValueError("DuckStation settings changed during trace")
    pc_counts: dict[str, int] = {}
    for hit in hits:
        pc_counts[str(hit["pc"])] = pc_counts.get(str(hit["pc"]), 0) + 1
    report = {
        "schema_version": 1,
        "observation_id": "FONT-WRITER-001",
        "status": "HITS_CAPTURED" if hits else "NO_HITS",
        "session_id": args.session_id,
        "started_at": started_at,
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "baseline_sha256": actual_hashes,
        "trigger_screenshot": screenshot_target.relative_to(project).as_posix(),
        "trigger_screenshot_sha256": sha256(screenshot_target),
        "watch_addresses": [f"0x{address:08X}" for address in WATCH_ADDRESSES],
        "breakpoint_replies": breakpoint_replies,
        "pc_hit_counts": pc_counts,
        "hits": hits,
        "gdb_server": {"host": "127.0.0.1", "port": args.port, "qSupported": (supported or b"").decode("ascii", errors="replace"), "initial_status": (initial_status or b"").decode("ascii", errors="replace")},
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "pc_hit_counts": pc_counts}, ensure_ascii=False), flush=True)
    print(report_path, flush=True)


if __name__ == "__main__":
    main()
