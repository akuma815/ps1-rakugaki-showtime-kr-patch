#!/usr/bin/env python3
"""Trace low-frequency callback dispatches while navigating to character select.

0x8008C8A8 is not itself an asset-directory walker.  It queues or invokes
callback(a1, a3), optionally copying a2 bytes from a1 into its queue.  Logging
the callback identity and pointed payloads gives a low-overhead transition log
without breaking on every LoadImage call.
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import shutil
import struct
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from capture_duckstation_ram import connect_with_retry
from capture_ui_ram_stages import wait_for_new_screenshot


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DISPATCH_ENTRY = 0x8008C8A8
REG_A0, REG_A1, REG_A2, REG_A3, REG_SP, REG_RA = 4, 5, 6, 7, 29, 31
RAM_BASE = 0x80000000
RAM_MIRROR_SIZE = 8 * 1024 * 1024
SCRATCHPAD_BASE = 0x1F800000
SCRATCHPAD_SIZE = 1024
CRITICAL_SETTINGS = {"Renderer": "Vulkan", "EnableGDBServer": "true", "GDBServerPort": "2345"}


def verify_settings(raw: bytes, port: int) -> None:
    parser = configparser.ConfigParser(strict=False)
    parser.read_string(raw.decode("utf-8"))
    expected = dict(CRITICAL_SETTINGS)
    expected["GDBServerPort"] = str(port)
    for key, want in expected.items():
        found = None
        for section in parser.sections():
            if parser.has_option(section, key):
                found = parser.get(section, key)
                break
        if found != want:
            raise ValueError(f"DuckStation setting {key} is {found!r}, expected {want!r}")


def read_regs(client) -> list[int]:
    for _ in range(8):
        # DuckStation can queue an extra stop reply around a software
        # breakpoint.  Treating that packet as the reply to `g` and then
        # waiting for unsolicited data leaves the emulation paused forever.
        # Issue a fresh register request after every non-register packet.
        packet = client.send_packet("g") or b""
        try:
            # Newer DuckStation previews append unavailable special registers
            # as `xxxxxxxx`.  The first 32 little-endian GPRs remain valid.
            gpr_text = packet[:32 * 8].decode("ascii")
            raw = bytes.fromhex(gpr_text)
        except ValueError:
            continue
        if len(raw) == 32 * 4:
            return [struct.unpack_from("<I", raw, i * 4)[0] for i in range(32)]
    raise RuntimeError("could not read registers")


def normalize_ram_pointer(value: int) -> int | None:
    """Return a KSEG0 address within the PS1's 8 MiB RAM mirror window."""
    physical = value & 0x1FFFFFFF
    if physical >= RAM_MIRROR_SIZE:
        return None
    if value >> 29 not in (0, 4, 5):  # physical, KSEG0, KSEG1
        return None
    return RAM_BASE | physical


def normalize_readable_pointer(value: int) -> tuple[int, int, str] | None:
    ram = normalize_ram_pointer(value)
    if ram is not None:
        return ram, RAM_BASE + RAM_MIRROR_SIZE, "ram_mirror"
    if SCRATCHPAD_BASE <= value < SCRATCHPAD_BASE + SCRATCHPAD_SIZE:
        return value, SCRATCHPAD_BASE + SCRATCHPAD_SIZE, "scratchpad"
    return None


def snapshot(client, value: int, size: int) -> dict:
    readable = normalize_readable_pointer(value)
    result = {"value": f"0x{value:08X}", "is_readable_pointer": readable is not None}
    if readable is None or size <= 0:
        return result
    normalized, region_end, region = readable
    take = min(size, region_end - normalized)
    try:
        data = client.read_memory(normalized, take, chunk_size=min(take, 0x400))
    except Exception as error:
        result["read_error"] = f"{type(error).__name__}:{error}"
        return result
    result.update({
        "normalized": f"0x{normalized:08X}",
        "region": region,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "hex": data.hex().upper(),
    })
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--duckstation", type=Path, default=Path(r"H:\Duckstation"))
    parser.add_argument("--session", default="charselect_001")
    parser.add_argument("--duration", type=float, default=180.0)
    parser.add_argument("--arm-at", type=float, default=2.0)
    parser.add_argument("--arm-timeout", type=float, default=60.0,
                        help="seconds to wait for ARA code to appear after --arm-at")
    parser.add_argument("--arm-on-f10", action="store_true",
                        help="run freely until a new DuckStation F10 screenshot appears")
    parser.add_argument("--trigger-timeout", type=float, default=600.0)
    parser.add_argument("--port", type=int, default=2345)
    parser.add_argument("--payload-bytes", type=int, default=256)
    parser.add_argument("--argument-bytes", type=int, default=64)
    parser.add_argument("--flush-every", type=float, default=10.0)
    parser.add_argument("--max-calls", type=int, default=2048)
    args = parser.parse_args()
    if (args.duration <= 0 or args.arm_at < 0 or args.arm_timeout <= 0
            or args.payload_bytes < 0 or args.argument_bytes < 0):
        raise ValueError("duration and snapshot sizes must be non-negative, with duration > 0")

    project = args.project.resolve()
    duckstation = args.duckstation.resolve()
    exe = duckstation / "duckstation-qt-x64-ReleaseLTCG.exe"
    settings_path = duckstation / "settings.ini"
    verify_settings(settings_path.read_bytes(), args.port)

    build = json.loads((project / "work/analysis/hangul_atlas_001_build.json").read_text(encoding="utf-8"))
    cue = project / build["output"]["cue"]
    static_report = json.loads((project / "work/analysis/callback_dispatch_static_001.json").read_text(encoding="utf-8"))
    ara = (project / static_report["source"]["path"]).read_bytes()
    entry_offset = int(static_report["dispatcher"]["file_offset"], 16)
    expected_entry = ara[entry_offset : entry_offset + 16]

    report_path = project / "work/analysis" / f"callback_dispatch_trace_{args.session}.json"
    if report_path.exists():
        raise FileExistsError(f"refusing to overwrite: {report_path}")

    calls: list[dict] = []
    desynced = 0
    status = "CAPTURED"
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    armed_at = None
    trigger_record = None

    def write_report(current_status: str) -> None:
        callback_counts: dict[str, int] = {}
        for call in calls:
            key = call["callback"]["value"]
            callback_counts[key] = callback_counts.get(key, 0) + 1
        report_path.write_text(json.dumps({
            "schema_version": 1,
            "observation_id": "CALLBACK-DISPATCH-TRACE-001",
            "session": args.session,
            "status": current_status,
            "baseline": {
                "cue": cue.relative_to(project).as_posix(),
                "bin_sha256": build["output"]["bin_sha256"],
                "duckstation": str(exe),
                "breakpoint": f"0x{DISPATCH_ENTRY:08X}",
                "static_evidence": "work/analysis/callback_dispatch_static_001.json",
            },
            "started_at": started_at,
            "armed_at": armed_at,
            "trigger": trigger_record,
            "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "abi": "a0=callback, a1=payload pointer/callback a0, a2=queued copy bytes, a3=callback a1",
            "call_count": len(calls),
            "callback_counts": callback_counts,
            "desynced_stops": desynced,
            "calls": calls,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    process = subprocess.Popen([str(exe), "-batch", "-fastboot", "--", str(cue)], cwd=duckstation)
    client = None
    try:
        client = connect_with_retry(args.port, 20)
        client.send_packet("qSupported:multiprocess+;swbreak+;hwbreak+")
        client.send_packet("?")
        client.resume()
        if args.arm_on_f10:
            screenshots_root = duckstation / "screenshots"
            baseline_screenshots = {path.resolve() for path in screenshots_root.glob("*.png")}
            print("navigate to the screen before character select and press F10", flush=True)
            screenshot = wait_for_new_screenshot(
                screenshots_root, baseline_screenshots, set(), process, args.trigger_timeout
            )
            evidence_dir = project / "work/runtime" / f"callback_dispatch_{args.session}"
            evidence_dir.mkdir(parents=True, exist_ok=False)
            screenshot_target = evidence_dir / screenshot.name
            shutil.copy2(screenshot, screenshot_target)
            trigger_record = {
                "kind": "F10 screenshot",
                "source": str(screenshot),
                "evidence": screenshot_target.relative_to(project).as_posix(),
                "sha256": hashlib.sha256(screenshot_target.read_bytes()).hexdigest(),
            }
        elif args.arm_at:
            print(f"running unarmed for {args.arm_at:.1f}s")
            deadline = time.monotonic() + args.arm_at
            while time.monotonic() < deadline:
                time.sleep(min(0.25, deadline - time.monotonic()))
        arm_deadline = time.monotonic() + args.arm_timeout
        runtime_entry = b""
        while time.monotonic() < arm_deadline:
            client.interrupt()
            runtime_entry = client.read_memory(DISPATCH_ENTRY, len(expected_entry), chunk_size=len(expected_entry))
            if runtime_entry == expected_entry:
                break
            client.resume()
            time.sleep(0.25)
        else:
            raise TimeoutError(f"ARA dispatcher did not load: {runtime_entry.hex().upper()}")
        reply = client.send_packet(f"Z0,{DISPATCH_ENTRY:x},4")
        if reply != b"OK":
            raise RuntimeError(f"breakpoint install failed: {reply!r}")
        armed_at = datetime.now().astimezone().isoformat(timespec="seconds")
        client.resume()
        if args.arm_on_f10:
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_OK)
            except Exception:
                pass
        print("callback dispatcher armed -- enter the target screen now", flush=True)

        trace_start = time.monotonic()
        deadline = trace_start + args.duration
        next_flush = time.monotonic() + args.flush_every
        while time.monotonic() < deadline and len(calls) < args.max_calls:
            old_timeout = client.socket.gettimeout()
            client.socket.settimeout(max(1.0, deadline - time.monotonic()))
            try:
                client.recv_packet()
            except Exception:
                break
            finally:
                client.socket.settimeout(old_timeout)
            try:
                regs = read_regs(client)
            except Exception:
                desynced += 1
                client.resume()
                continue

            callback, payload, payload_size, callback_arg = (regs[REG_A0], regs[REG_A1], regs[REG_A2], regs[REG_A3])
            payload_preview = args.payload_bytes if payload_size == 0 else min(args.payload_bytes, payload_size)
            calls.append({
                "index": len(calls),
                "elapsed_s": round(time.monotonic() - trace_start, 3),
                "callback": snapshot(client, callback, 32),
                "payload": snapshot(client, payload, payload_preview),
                "payload_copy_bytes": payload_size,
                "callback_argument": snapshot(client, callback_arg, args.argument_bytes),
                "return_address": snapshot(client, regs[REG_RA], 16),
                "stack": snapshot(client, regs[REG_SP], 64),
                "saved": [f"0x{regs[16 + i]:08X}" for i in range(8)],
            })
            client.resume()
            if time.monotonic() >= next_flush:
                write_report("CAPTURING")
                next_flush = time.monotonic() + args.flush_every
    except Exception as error:
        status = f"ABORTED:{type(error).__name__}:{error}"
    finally:
        if client is not None:
            try:
                client.interrupt()
                client.send_packet(f"z0,{DISPATCH_ENTRY:x},4")
                client.resume()
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
        write_report(status)

    print(json.dumps({"status": status, "calls": len(calls), "desynced_stops": desynced,
                      "output": report_path.relative_to(project).as_posix()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
