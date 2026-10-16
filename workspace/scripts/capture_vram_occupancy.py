#!/usr/bin/env python3
"""Audit runtime occupancy of the candidate second-bank VRAM page via injected GP0 C0h readbacks.

FONT-TWO-BANK-VRAM-002: the DuckStation GDB server in the pinned baseline build
supports memory reads/writes, breakpoints, and stepping, but no register writes
(`G`/`P` both answer empty). Each probe therefore hijacks execution in place:

1. interrupt; take the halt pc `F` and verify the three instructions at
   F-4/F/F+4 are relocatable (no control flow, F not a delay slot), stepping
   forward until a safe `F` is found;
2. write a self-contained MIPS stub to scratch RAM, patch `F` with
   `j stub; nop` (originals saved), and arm a Z0 breakpoint at `F+8`;
3. resume: the stub saves every register it touches, disables interrupts via
   SR, waits for GPU idle, issues GP0 C0h (VRAM->CPU) for the candidate rect,
   streams GPUREAD into a scratch buffer, restores the DMA direction, SR, and
   registers, then executes the two relocated original instructions and jumps
   to `F+8`, where the breakpoint fires;
4. read the buffer, byte-restore the patched code and scratch RAM, resume.

The game state is fully restored after each probe; the only non-reverted side
effect is the consumed GPU read cursor. Every probe also captures a window
screenshot so scenes can be labeled afterwards.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import re
import socket
import struct
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from PIL import ImageGrab

from capture_duckstation_ram import RspClient, connect_with_retry


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# Two byte-level serializations of the same verified settings: DuckStation
# rewrites settings.ini on graceful exit and flips between these two forms.
# Critical values (renderer, PGXP, dumps off, GDB 2345) were reverified for both.
EXPECTED_SETTINGS_SHA256S = {
    "02037cc124559e370bdf7dea165806c272ee97b835fcd61bfa732567219e9c9d",
    "09d2a47afc2f773382f0203c157b1a3debed3ebd846faa80b4079b9fd2f5629d",
}

RECT_X, RECT_Y, RECT_W_WORDS, RECT_H = 704, 256, 64, 256
RECT_BYTES = RECT_W_WORDS * RECT_H * 2  # fixed buffer budget; overridden rects must fit

SCRATCH_BASE = 0x80017000
REGSAVE_OFF = 0x220
HEADER_OFF = 0x248
BUFFER_OFF = 0x260
FOOTPRINT = BUFFER_OFF + RECT_BYTES

RAM_LO, RAM_HI = 0x80000000, 0x80200000

REG_PC = 37

TRAMP_MARKER = bytes.fromhex("AD DE 00 34 AE DE 00 34 AF DE 00 34 B0 DE 00 34".replace(" ", ""))

CONTROL_FLOW_MNEMONICS = {
    "j", "jal", "jr", "jalr", "syscall", "break", "eret", "rfe",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_window(pid: int) -> int:
    result: list[int] = []
    user32 = ctypes.windll.user32
    enum_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd: int, _lparam: int) -> bool:
        window_pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if window_pid.value == pid and user32.IsWindowVisible(hwnd):
            result.append(hwnd)
        return True

    user32.EnumWindows(enum_type(callback), 0)
    return result[0] if result else 0


def capture_window(pid: int, target: Path) -> bool:
    user32 = ctypes.windll.user32
    hwnd = find_window(pid)
    if not hwnd:
        return False
    rectangle = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rectangle)):
        return False
    if rectangle.right <= rectangle.left or rectangle.bottom <= rectangle.top:
        return False
    image = ImageGrab.grab(bbox=(rectangle.left, rectangle.top, rectangle.right, rectangle.bottom), all_screens=True)
    image.save(target)
    return True


def assemble_stub(rect: tuple[int, int, int, int]) -> tuple[bytes, int]:
    """Assemble the probe stub; returns (code, trampoline_offset)."""
    import capstone
    import keystone

    rect_x, rect_y, rect_w_words, rect_h = rect
    if rect_w_words * rect_h * 2 > RECT_BYTES:
        raise ValueError("rect exceeds scratch buffer budget")
    base_hi = (SCRATCH_BASE >> 16) & 0xFFFF
    header = (SCRATCH_BASE + HEADER_OFF) & 0xFFFF
    save = (SCRATCH_BASE + REGSAVE_OFF) & 0xFFFF
    xy = ((rect_y & 0x1FF) << 16) | (rect_x & 0x3FF)
    wh = ((rect_h & 0xFFFF) << 16) | (rect_w_words & 0xFFFF)
    words = rect_w_words * rect_h // 2
    source = f"""
        lui   $26, 0x{base_hi:04X}
        sw    $8, 0x{save:04X}($26)
        lui   $8, 0x{base_hi:04X}
        sw    $9,  0x{save + 4:04X}($8)
        sw    $10, 0x{save + 8:04X}($8)
        sw    $11, 0x{save + 12:04X}($8)
        sw    $12, 0x{save + 16:04X}($8)
        sw    $13, 0x{save + 20:04X}($8)
        sw    $24, 0x{save + 24:04X}($8)
        sw    $25, 0x{save + 28:04X}($8)
        mfc0  $9, $12
        nop
        sw    $9, 0x{save + 32:04X}($8)
        addiu $10, $0, -2
        and   $10, $9, $10
        mtc0  $10, $12
        nop
        nop
        lui   $25, 0x{base_hi:04X}
        ori   $25, $25, 0x{header:04X}
        lw    $8, 12($25)
        nop
        bne   $8, $0, exit
        nop
        ori   $8, $0, 1
        sw    $8, 12($25)
        lui   $24, 0x1F80
        lw    $8, 0x1814($24)
        nop
        sw    $8, 0($25)
        lui   $12, 0x1400
        lui   $11, 0x0010
    wait1:
        lw    $8, 0x1814($24)
        nop
        and   $13, $8, $12
        beq   $13, $12, idle_ok
        nop
        addiu $11, $11, -1
        bne   $11, $0, wait1
        nop
        ori   $8, $0, 1
        sw    $8, 4($25)
        beq   $0, $0, done_mark
        nop
    idle_ok:
        sw    $0, 4($25)
        lui   $8, 0x0400
        sw    $8, 0x1814($24)
        lui   $8, 0xC000
        sw    $8, 0x1810($24)
        lui   $8, 0x{(xy >> 16) & 0xFFFF:04X}
        ori   $8, $8, 0x{xy & 0xFFFF:04X}
        sw    $8, 0x1810($24)
        lui   $8, 0x{(wh >> 16) & 0xFFFF:04X}
        ori   $8, $8, 0x{wh & 0xFFFF:04X}
        sw    $8, 0x1810($24)
        lui   $12, 0x0800
        lui   $11, 0x0010
    wait2:
        lw    $8, 0x1814($24)
        nop
        and   $13, $8, $12
        bne   $13, $0, read_ok
        nop
        addiu $11, $11, -1
        bne   $11, $0, wait2
        nop
        ori   $8, $0, 2
        sw    $8, 4($25)
        beq   $0, $0, restore_dma
        nop
    read_ok:
        addiu $9, $25, 0x{BUFFER_OFF - HEADER_OFF:X}
        ori   $10, $0, {words}
    loop:
        lw    $8, 0x1810($24)
        nop
        sw    $8, 0($9)
        addiu $9, $9, 4
        addiu $10, $10, -1
        bne   $10, $0, loop
        nop
    restore_dma:
        lw    $8, 0($25)
        nop
        srl   $8, $8, 29
        andi  $8, $8, 3
        lui   $13, 0x0400
        or    $8, $8, $13
        sw    $8, 0x1814($24)
        lw    $8, 0x1814($24)
        nop
        sw    $8, 8($25)
    done_mark:
        ori   $8, $0, 1
        sw    $8, 16($25)
    exit:
        lui   $8, 0x{base_hi:04X}
        lw    $9, 0x{save + 32:04X}($8)
        nop
        mtc0  $9, $12
        nop
        lw    $9,  0x{save + 4:04X}($8)
        lw    $10, 0x{save + 8:04X}($8)
        lw    $11, 0x{save + 12:04X}($8)
        lw    $12, 0x{save + 16:04X}($8)
        lw    $13, 0x{save + 20:04X}($8)
        lw    $24, 0x{save + 24:04X}($8)
        lw    $25, 0x{save + 28:04X}($8)
        lw    $8,  0x{save + 0:04X}($8)
        nop
        ori   $0, $0, 0xDEAD
        ori   $0, $0, 0xDEAE
        ori   $0, $0, 0xDEAF
        ori   $0, $0, 0xDEB0
    """
    ks = keystone.Ks(keystone.KS_ARCH_MIPS, keystone.KS_MODE_MIPS32 | keystone.KS_MODE_LITTLE_ENDIAN)
    encoding, _count = ks.asm(source, SCRATCH_BASE)
    code = bytes(encoding)
    if len(code) > REGSAVE_OFF:
        raise ValueError(f"stub too large: {len(code)} bytes")
    md = capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS32 | capstone.CS_MODE_LITTLE_ENDIAN)
    decoded = list(md.disasm(code, SCRATCH_BASE))
    if len(decoded) != len(code) // 4:
        raise ValueError("stub round-trip disassembly failed")
    tramp_offset = code.find(TRAMP_MARKER)
    if tramp_offset < 0 or code.find(TRAMP_MARKER, tramp_offset + 4) >= 0 or tramp_offset % 4:
        raise ValueError("trampoline marker not uniquely identified in stub")
    return code, tramp_offset


def encode_j(target: int) -> int:
    return 0x08000000 | ((target >> 2) & 0x03FFFFFF)


def is_relocatable(insn) -> bool:
    import capstone

    if insn.mnemonic in CONTROL_FLOW_MNEMONICS or insn.mnemonic.startswith("b"):
        return False
    for group in (capstone.CS_GRP_JUMP, capstone.CS_GRP_CALL, capstone.CS_GRP_RET, capstone.CS_GRP_BRANCH_RELATIVE):
        if insn.group(group):
            return False
    return True


def write_memory(client: RspClient, address: int, data: bytes, chunk_size: int = 0x2000) -> None:
    for offset in range(0, len(data), chunk_size):
        chunk = data[offset : offset + chunk_size]
        reply = client.send_packet(f"M{address + offset:x},{len(chunk):x}:{chunk.hex()}")
        if reply != b"OK":
            raise ValueError(f"GDB memory write failed at 0x{address + offset:08X}: {reply!r}")


STOP_REPLY_RE = re.compile(rb"^[ST][0-9a-fA-F]{2}")


def read_pc(client: RspClient) -> int:
    packet = client.send_packet("g") or b""
    for _attempt in range(3):
        try:
            raw = bytes.fromhex(packet.decode("ascii"))
        except ValueError:
            raw = None
        if raw is not None and len(raw) >= (REG_PC + 1) * 4:
            return struct.unpack_from("<I", raw, REG_PC * 4)[0]
        if STOP_REPLY_RE.match(packet):
            # A stop notification queued before our request arrived first; the
            # real register reply is still in the stream.
            packet = client.recv_packet()
            continue
        break
    raise SessionLostError(f"register read returned unusable reply {packet[:16]!r}")


class SessionLostError(RuntimeError):
    """The emulator or its GDB link is gone; abort the session but keep the report."""


def wait_stop(client: RspClient, timeout: float) -> bytes:
    old_timeout = client.socket.gettimeout()
    client.socket.settimeout(timeout)
    try:
        return client.recv_packet()
    finally:
        client.socket.settimeout(old_timeout)


def find_hijack_point(client: RspClient, md, max_steps: int = 32) -> tuple[int, bytes]:
    """With the target halted, find a pc whose surrounding instructions allow the patch."""
    for _ in range(max_steps):
        pc = read_pc(client)
        in_scratch = SCRATCH_BASE - 16 <= pc < SCRATCH_BASE + FOOTPRINT + 16
        if RAM_LO + 0x4000 <= pc < RAM_HI and pc % 4 == 0 and not in_scratch:
            window = client.read_memory(pc - 4, 12)
            insns = list(md.disasm(window, pc - 4))
            if len(insns) == 3 and all(is_relocatable(insn) for insn in insns):
                return pc, window[4:12]
        client.send_packet("s", expect_reply=False)
        wait_stop(client, 10.0)
    raise RuntimeError("no safe hijack point found")


def run_probe(client: RspClient, md, stub: bytes, tramp_offset: int, stop_timeout: float,
              rect_bytes: int = RECT_BYTES) -> dict:
    """Halt, execute the readback stub exactly once in-line, byte-restore state, and return evidence."""
    stop = client.interrupt()
    hijack_pc, original_pair = find_hijack_point(client, md)
    original_memory = client.read_memory(SCRATCH_BASE, FOOTPRINT)
    restored = {"patch": False, "scratch": False}
    breakpoint_addr = hijack_pc + 8
    try:
        staged = bytearray(FOOTPRINT)
        staged[: len(stub)] = stub
        struct.pack_into("<II", staged, tramp_offset, *struct.unpack("<II", original_pair))
        struct.pack_into("<II", staged, tramp_offset + 8, encode_j(breakpoint_addr), 0)
        struct.pack_into("<IIIII", staged, HEADER_OFF, 0, 0xFF, 0, 0, 0)
        staged[BUFFER_OFF:] = b"\xAA" * RECT_BYTES
        write_memory(client, SCRATCH_BASE, bytes(staged))
        write_memory(client, hijack_pc, struct.pack("<II", encode_j(SCRATCH_BASE), 0))

        reply = client.send_packet(f"Z0,{breakpoint_addr:x},4")
        if reply != b"OK":
            raise RuntimeError(f"return breakpoint install failed: {reply!r}")
        try:
            client.resume()
            stub_stop = wait_stop(client, stop_timeout)
        finally:
            client.send_packet(f"z0,{breakpoint_addr:x},4")
        landed_pc = read_pc(client)
        header = client.read_memory(SCRATCH_BASE + HEADER_OFF, 20)
        initial_stat, flag, final_stat, gate, done = struct.unpack("<IIIII", header)
        rect = client.read_memory(SCRATCH_BASE + BUFFER_OFF, rect_bytes)
    finally:
        try:
            write_memory(client, hijack_pc, original_pair)
            restored["patch"] = True
            write_memory(client, SCRATCH_BASE, original_memory)
            restored["scratch"] = True
        except Exception as error:  # pragma: no cover - defensive path
            print(f"STATE RESTORE FAILED: {error}", file=sys.stderr)
    return {
        "halt_stop_reply": stop.decode("ascii", errors="replace"),
        "hijack_pc": f"0x{hijack_pc:08X}",
        "stub_stop_reply": stub_stop.decode("ascii", errors="replace"),
        "landed_pc": f"0x{landed_pc:08X}",
        "stub_completed": landed_pc == breakpoint_addr and gate == 1 and done == 1,
        "gpustat_initial": f"0x{initial_stat:08X}",
        "gpustat_final": f"0x{final_stat:08X}",
        "stub_flag": flag,
        "state_restored": restored,
        "rect": rect,
    }


def main() -> None:
    import capstone

    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--duckstation", type=Path, default=Path(r"H:\Duckstation"))
    parser.add_argument("--session", default="001")
    parser.add_argument("--schedule", default="20,30,40,50,60,75,90,105,120,150,180,210,240")
    parser.add_argument("--port", type=int, default=2345)
    parser.add_argument("--stop-timeout", type=float, default=30.0)
    parser.add_argument("--rect", default=f"{RECT_X},{RECT_Y},{RECT_W_WORDS},{RECT_H}",
                        help="one or more x,y,w_words,h VRAM rects separated by ';'; "
                             "multiple rects are probed round-robin")
    parser.add_argument("--cue", type=Path, default=None,
                        help="alternate CUE to boot (e.g. a PoC build); its BIN hash must be "
                             "given via --bin-sha256")
    parser.add_argument("--bin-sha256", default=None,
                        help="expected SHA-256 of the BIN referenced by --cue")
    args = parser.parse_args()
    if (args.cue is None) != (args.bin_sha256 is None):
        raise ValueError("--cue and --bin-sha256 must be given together")
    rect_specs = []
    for chunk in args.rect.split(";"):
        rect_spec = tuple(int(value) for value in chunk.split(","))
        if len(rect_spec) != 4:
            raise ValueError("--rect expects x,y,w_words,h groups separated by ';'")
        rect_specs.append(rect_spec)
    project = args.project.resolve()
    duckstation = args.duckstation.resolve()
    exe = duckstation / "duckstation-qt-x64-ReleaseLTCG.exe"
    settings = duckstation / "settings.ini"
    cue = duckstation / "Roms/Rakugaki Showtime (J)/Rakugaki Showtime (Japan).cue"

    output_dir = project / "work/runtime" / f"vram_occupancy_{args.session}"
    report_path = project / "work/analysis" / f"font_two_bank_vram_002_capture_{args.session}.json"
    if output_dir.exists() or report_path.exists():
        raise FileExistsError(f"refusing to overwrite session outputs: {output_dir}")

    settings_start_sha = sha256_file(settings)
    if settings_start_sha not in EXPECTED_SETTINGS_SHA256S:
        raise ValueError("DuckStation settings baseline mismatch")
    expected_disc = json.loads((project / "workspace/config/source_manifest.json").read_text(encoding="utf-8"))["disc"]
    if args.cue is not None:
        cue = args.cue.resolve()
        bin_path = cue.parent / re.search(r'(?im)^FILE\s+"([^"]+)"\s+BINARY$', cue.read_text(encoding="ascii")).group(1)
        expected_disc = {"bin_sha256": args.bin_sha256, "cue_sha256": hashlib.sha256(cue.read_bytes()).hexdigest()}
        if sha256_file(bin_path) != expected_disc["bin_sha256"]:
            raise ValueError("override BIN does not match --bin-sha256")
    else:
        if hashlib.sha256(cue.read_bytes()).hexdigest() != expected_disc["cue_sha256"]:
            raise ValueError("DuckStation CUE does not match project baseline")
        if sha256_file(cue.with_suffix(".bin")) != expected_disc["bin_sha256"]:
            raise ValueError("DuckStation BIN does not match project baseline")

    stubs = [assemble_stub(spec) for spec in rect_specs]
    md = capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS32 | capstone.CS_MODE_LITTLE_ENDIAN)
    md.detail = True
    schedule = sorted(float(value) for value in args.schedule.split(","))
    output_dir.mkdir(parents=True)

    command = [str(exe), "-batch", "-fastboot", "--", str(cue)]
    process = subprocess.Popen(command, cwd=duckstation)
    client = None
    probes = []
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        client = connect_with_retry(args.port, 20)
        client.send_packet("qSupported:multiprocess+;swbreak+;hwbreak+")
        client.send_packet("?")
        client.resume()
        time.sleep(1.0)

        # Tooling self-test: M-write support.
        client.interrupt()
        original = client.read_memory(SCRATCH_BASE, 8)
        write_memory(client, SCRATCH_BASE, bytes.fromhex("DEADBEEFDEADBEEF"))
        if client.read_memory(SCRATCH_BASE, 8) != bytes.fromhex("DEADBEEFDEADBEEF"):
            raise RuntimeError("GDB M-write verification failed")
        write_memory(client, SCRATCH_BASE, original)
        client.resume()

        emulation_start = time.monotonic()
        for index, probe_time in enumerate(schedule):
            remaining = probe_time - (time.monotonic() - emulation_start)
            if remaining > 0:
                time.sleep(remaining)
            screen_path = output_dir / f"probe_{index:03d}_screen.png"
            screen_ok = capture_window(process.pid, screen_path)
            spec = rect_specs[index % len(rect_specs)]
            stub, tramp_offset = stubs[index % len(rect_specs)]
            base_entry = {
                "index": index,
                "scheduled_seconds": probe_time,
                "wall_seconds": round(time.monotonic() - emulation_start, 2),
                "rect_words": list(spec),
                "screenshot": screen_path.relative_to(project).as_posix() if screen_ok else None,
            }
            try:
                result = run_probe(client, md, stub, tramp_offset, args.stop_timeout,
                                   spec[2] * spec[3] * 2)
            except SessionLostError as error:
                probes.append({**base_entry, "aborted": str(error)})
                print(f"probe {index:03d} @{probe_time:.0f}s SESSION LOST: {error}")
                break
            except (ConnectionError, TimeoutError, OSError) as error:
                probes.append({**base_entry, "aborted": f"{type(error).__name__}: {error}"})
                print(f"probe {index:03d} @{probe_time:.0f}s SESSION LOST: {error}")
                break
            except RuntimeError as error:
                probes.append({**base_entry, "skipped": str(error)})
                print(f"probe {index:03d} @{probe_time:.0f}s SKIPPED: {error}")
                client.resume()
                continue
            rect = result.pop("rect")
            rect_path = output_dir / f"probe_{index:03d}_rect.bin"
            rect_path.write_bytes(rect)
            probes.append({
                **base_entry,
                "rect_file": rect_path.relative_to(project).as_posix(),
                "rect_sha256": hashlib.sha256(rect).hexdigest(),
                **result,
            })
            print(f"probe {index:03d} @{probe_time:.0f}s rect={spec} flag={result['stub_flag']} "
                  f"completed={result['stub_completed']} rect_sha={probes[-1]['rect_sha256'][:16]}")
            client.resume()
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

    settings_end_sha = sha256_file(settings)
    if settings_end_sha not in EXPECTED_SETTINGS_SHA256S:
        raise ValueError("DuckStation settings changed during capture")

    unique_hashes = sorted({probe["rect_sha256"] for probe in probes if "rect_sha256" in probe})
    aborted = any("aborted" in probe for probe in probes)
    all_completed = bool(probes) and all(
        probe.get("stub_completed") and probe.get("stub_flag") == 0 for probe in probes
    )
    report = {
        "schema_version": 1,
        "observation_id": "FONT-TWO-BANK-VRAM-002",
        "session": args.session,
        "status": ("SESSION_LOST_PARTIAL" if aborted
                   else "CAPTURED" if all_completed else "CAPTURED_WITH_ANOMALIES"),
        "started_at": started_at,
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "disc": {"bin_sha256": expected_disc["bin_sha256"], "cue_sha256": expected_disc["cue_sha256"]},
        "settings_sha256": {"start": settings_start_sha, "end": settings_end_sha},
        "rect_words": list(rect_specs[0]),
        "rect_words_sets": [list(spec) for spec in rect_specs],
        "method": "in-line pc hijack (j stub) + interrupt-disabled GP0 C0h readback stub, full byte restore per probe; no GDB register writes required",
        "scratch": {"base": f"0x{SCRATCH_BASE:08X}", "footprint": FOOTPRINT},
        "stub_sha256s": [hashlib.sha256(code).hexdigest() for code, _offset in stubs],
        "probe_count": len(probes),
        "unique_rect_hashes": unique_hashes,
        "probes": probes,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "probes": len(probes), "unique_rect_hashes": len(unique_hashes)}, indent=2))


if __name__ == "__main__":
    main()
