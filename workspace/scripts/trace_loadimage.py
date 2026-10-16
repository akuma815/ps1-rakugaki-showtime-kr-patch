#!/usr/bin/env python3
"""Log every LoadImage(RECT*, src*) call while the user plays (LOADIMAGE-TRACE-001).

Guessing a graphic's disc offset from a texture dump is unreliable: the mask
match can miss, and the search region is a guess. The game itself knows the
answer, so break on the upload routine instead and read its arguments:

    a0 -> RECT { short x, y, w, h }   destination in VRAM
    a1 -> source pointer              RAM address of the pixel data

Mapping the source RAM address back to the disc is then a subtraction, because
each container block is loaded contiguously. Every menu label, banner and
in-game word the player walks past is recorded exactly once, with no searching.
"""

from __future__ import annotations

import argparse
import configparser
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

LOADIMAGE_ENTRY = 0x8008C2D8
REG_A0, REG_A1, REG_RA = 4, 5, 31
CRITICAL_SETTINGS = {"Renderer": "Vulkan", "EnableGDBServer": "true", "GDBServerPort": "2345"}


def verify_settings(raw: bytes) -> None:
    parser = configparser.ConfigParser(strict=False)
    parser.read_string(raw.decode("utf-8"))
    for key, want in CRITICAL_SETTINGS.items():
        found = None
        for section in parser.sections():
            if parser.has_option(section, key):
                found = parser.get(section, key)
                break
        if found != want:
            raise ValueError(f"DuckStation setting {key} is {found!r}, expected {want!r}")


def read_regs(client) -> list[int]:
    """Read the GPR block, tolerating a desynced packet stream.

    A stray stop-reply or an out-of-order packet used to raise straight out of
    the capture loop, which killed the emulator mid-boot and -- because the
    report was written after the loop -- threw away the whole session. Resyncing
    is cheap; losing a play session is not.
    """
    for _ in range(8):
        # A second stop reply can be queued around a software breakpoint.  It
        # is not a register packet; request registers again instead of waiting
        # for unsolicited data while the emulator remains paused.
        packet = client.send_packet("g") or b""
        try:
            # Newer DuckStation previews append unavailable special registers
            # as `xxxxxxxx`; only the first 32 GPRs are needed here.
            gpr_text = packet[:32 * 8].decode("ascii")
            raw = bytes.fromhex(gpr_text)
        except ValueError:
            continue
        if len(raw) == 32 * 4:
            return [struct.unpack_from("<I", raw, i * 4)[0] for i in range(32)]
    raise RuntimeError("could not read registers")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--duckstation", type=Path, default=Path(r"H:\Duckstation"))
    parser.add_argument("--session", default="001")
    parser.add_argument("--duration", type=float, default=600.0)
    parser.add_argument("--arm-at", type=float, default=0.0,
                        help="seconds to run at full speed before installing the breakpoint; "
                             "LoadImage is called many times per frame, so an armed trace is far too "
                             "slow to navigate menus with. Arm late and the player only has to make "
                             "the one input that loads the screen under test.")
    parser.add_argument("--arm-window", type=float, default=0.0,
                        help="seconds to keep the breakpoint installed (0 = until --duration)")
    parser.add_argument("--port", type=int, default=2345)
    parser.add_argument("--signature-bytes", type=int, default=96,
                        help="bytes of source pixel data recorded per upload for exact disc lookup")
    parser.add_argument("--flush-every", type=float, default=20.0,
                        help="seconds between incremental writes so a killed session keeps its data")
    args = parser.parse_args()
    project = args.project.resolve()
    duckstation = args.duckstation.resolve()
    exe = duckstation / "duckstation-qt-x64-ReleaseLTCG.exe"
    build = json.loads((project / "work/analysis/hangul_atlas_001_build.json").read_text(encoding="utf-8"))
    cue = project / build["output"]["cue"]
    settings_path = duckstation / "settings.ini"
    verify_settings(settings_path.read_bytes())

    report_path = project / "work/analysis" / f"loadimage_trace_{args.session}.json"
    if report_path.exists():
        raise FileExistsError(f"refusing to overwrite: {report_path}")

    def write_report(status: str) -> int:
        records = sorted(calls.values(), key=lambda r: (r["rect"][1], r["rect"][0], r["source"]))
        report_path.write_text(json.dumps({
            "schema_version": 1,
            "observation_id": "LOADIMAGE-TRACE-001",
            "session": args.session,
            "status": status,
            "started_at": started_at,
            "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "breakpoint": f"0x{LOADIMAGE_ENTRY:08X}",
            "abi": "a0 = RECT* {x,y,w,h}, a1 = source pointer",
            "unique_uploads": len(records),
            "desynced_stops": desynced,
            "uploads": records,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return len(records)

    process = subprocess.Popen([str(exe), "-batch", "-fastboot", "--", str(cue)], cwd=duckstation)
    client = None
    calls: dict[tuple, dict] = {}
    desynced = 0
    status = "CAPTURED"
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        client = connect_with_retry(args.port, 20)
        client.send_packet("qSupported:multiprocess+;swbreak+;hwbreak+")
        client.send_packet("?")
        client.resume()
        started = time.monotonic()
        if args.arm_at > 0:
            print(f"running unarmed for {args.arm_at:.0f}s -- navigate now")
            while time.monotonic() - started < args.arm_at:
                time.sleep(0.5)
        else:
            time.sleep(2.0)
        client.interrupt()
        reply = client.send_packet(f"Z0,{LOADIMAGE_ENTRY:x},4")
        if reply != b"OK":
            raise RuntimeError(f"breakpoint install failed: {reply!r}")
        client.resume()
        print("breakpoint armed -- enter the screen under test now")

        window = args.arm_window if args.arm_window > 0 else args.duration
        deadline = time.monotonic() + window
        next_flush = time.monotonic() + args.flush_every
        while time.monotonic() < deadline:
            old = client.socket.gettimeout()
            client.socket.settimeout(max(1.0, deadline - time.monotonic()))
            try:
                client.recv_packet()
            except Exception:
                break
            finally:
                client.socket.settimeout(old)
            try:
                regs = read_regs(client)
            except Exception:
                # Resync and keep going: one unreadable stop is worth one
                # missed upload, not the rest of the session.
                desynced += 1
                client.resume()
                continue
            rect_ptr, src = regs[REG_A0], regs[REG_A1]
            try:
                rect = struct.unpack("<4h", client.read_memory(rect_ptr, 8, chunk_size=8))
            except Exception:
                rect = (0, 0, 0, 0)
            key = (src, rect)
            if key not in calls:
                # Record a signature of the pixels too. Source addresses alone
                # need a per-block RAM->disc delta that has to be guessed, and a
                # wrong guess silently drops the upload; the bytes can be found
                # on the disc directly.
                try:
                    signature = client.read_memory(src, args.signature_bytes,
                                                   chunk_size=args.signature_bytes).hex().upper()
                except Exception:
                    signature = ""
                # The RECT lives somewhere; if consecutive uploads read consecutive
                # rect pointers, that region IS the sprite descriptor table, and
                # ra names the routine that walks it. The breakpoint sits on the
                # very first instruction of LoadImage, so s0-s7 still hold the
                # caller's values -- the directory walker keeps its cursor there.
                calls[key] = {"source": f"0x{src:08X}", "rect": list(rect), "signature": signature,
                              "rect_ptr": f"0x{rect_ptr:08X}", "ra": f"0x{regs[REG_RA]:08X}",
                              "saved": [f"0x{regs[16 + i]:08X}" for i in range(8)],
                              "count": 0, "first_seen_s": round(time.monotonic() - (deadline - args.duration), 1)}
            calls[key]["count"] += 1
            client.resume()
            # Flush periodically: a session stopped early still keeps its data.
            if time.monotonic() >= next_flush:
                write_report("CAPTURING")
                next_flush = time.monotonic() + args.flush_every
    except Exception as error:
        status = f"ABORTED:{type(error).__name__}:{error}"
    finally:
        if client is not None:
            try:
                client.interrupt()
                client.send_packet(f"z0,{LOADIMAGE_ENTRY:x},4")
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

        total = write_report(status)
    print(json.dumps({"status": status, "unique_uploads": total,
                      "desynced_stops": desynced}, indent=2))


if __name__ == "__main__":
    main()
