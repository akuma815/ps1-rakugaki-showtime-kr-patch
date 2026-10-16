#!/usr/bin/env python3
"""Launch DuckStation headlessly, capture PS1 RAM via its GDB server, and scan font evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import subprocess
import time
from pathlib import Path

from psx_tim import parse_tim


RAM_BASE = 0x80000000
RAM_SIZE = 2 * 1024 * 1024


class RspClient:
    def __init__(self, host: str, port: int):
        self.socket = socket.create_connection((host, port), timeout=10)
        self.socket.settimeout(20)

    def close(self) -> None:
        self.socket.close()

    def _recv_byte(self) -> bytes:
        value = self.socket.recv(1)
        if not value:
            raise ConnectionError("GDB server closed the connection")
        return value

    def recv_packet(self) -> bytes:
        while True:
            value = self._recv_byte()
            if value == b"$":
                break
        payload = bytearray()
        while True:
            value = self._recv_byte()
            if value == b"#":
                break
            payload.extend(value)
        checksum = int(self.socket.recv(2), 16)
        if sum(payload) & 0xFF != checksum:
            self.socket.sendall(b"-")
            raise ValueError("bad GDB packet checksum")
        self.socket.sendall(b"+")
        return bytes(payload)

    def send_packet(self, payload: str, expect_reply: bool = True) -> bytes | None:
        encoded = payload.encode("ascii")
        checksum = sum(encoded) & 0xFF
        self.socket.sendall(b"$" + encoded + f"#{checksum:02x}".encode("ascii"))
        ack = self._recv_byte()
        if ack != b"+":
            raise ValueError(f"GDB server rejected packet {payload!r}: {ack!r}")
        return self.recv_packet() if expect_reply else None

    def interrupt(self) -> bytes:
        self.socket.sendall(b"\x03")
        return self.recv_packet()

    def resume(self) -> None:
        self.send_packet("c", expect_reply=False)

    def read_memory(self, address: int, size: int, chunk_size: int = 0x2000) -> bytes:
        output = bytearray()
        for offset in range(0, size, chunk_size):
            take = min(chunk_size, size - offset)
            reply = self.send_packet(f"m{address + offset:x},{take:x}")
            if reply is None or reply.startswith(b"E"):
                raise ValueError(f"GDB memory read failed at 0x{address + offset:08X}: {reply!r}")
            chunk = bytes.fromhex(reply.decode("ascii"))
            if len(chunk) != take:
                raise ValueError(f"short GDB memory reply at 0x{address + offset:08X}")
            output.extend(chunk)
        return bytes(output)


def connect_with_retry(port: int, timeout: float) -> RspClient:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return RspClient("127.0.0.1", port)
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"DuckStation GDB server did not open port {port}")


def find_all(data: bytes, needle: bytes) -> list[int]:
    hits = []
    cursor = 0
    while needle:
        offset = data.find(needle, cursor)
        if offset < 0:
            break
        hits.append(offset)
        cursor = offset + 1
    return hits


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--duckstation", type=Path, default=Path(r"H:\Duckstation"))
    parser.add_argument("--times", default="3,8,15", help="seconds after GDB startup")
    parser.add_argument("--port", type=int, default=2345)
    args = parser.parse_args()
    project = args.project.resolve()
    duckstation = args.duckstation.resolve()
    exe = duckstation / "duckstation-qt-x64-ReleaseLTCG.exe"
    cue = duckstation / "Roms/Rakugaki Showtime (J)/Rakugaki Showtime (Japan).cue"
    expected_disc = json.loads((project / "workspace/config/source_manifest.json").read_text(encoding="utf-8"))["disc"]
    if hashlib.sha256(cue.read_bytes()).hexdigest() != expected_disc["cue_sha256"]:
        raise ValueError("DuckStation CUE does not match project baseline")
    bin_path = cue.with_suffix(".bin")
    digest = hashlib.sha256()
    with bin_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected_disc["bin_sha256"]:
        raise ValueError("DuckStation BIN does not match project baseline")

    targets = json.loads((project / "workspace/config/asset_targets.json").read_text(encoding="utf-8"))
    font_offset = targets["targets"]["primary_font_atlas"]["container_offset"]
    hagecho = (project / "extracted/HAGECHO.BIN").read_bytes()
    tim = parse_tim(hagecho, font_offset)
    if tim is None:
        raise ValueError("font target no longer parses")
    full_tim = hagecho[font_offset : font_offset + tim["total_size"]]
    image_payload = hagecho[tim["image"]["payload_offset"] : tim["image"]["payload_offset"] + tim["image"]["payload_size"]]
    clut_payload = hagecho[tim["clut"]["payload_offset"] : tim["clut"]["payload_offset"] + tim["clut"]["payload_size"]]

    times = sorted(float(value) for value in args.times.split(","))
    command = [str(exe), "-batch", "-nogui", "-fastboot", "--", str(cue)]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(command, cwd=duckstation, creationflags=creationflags)
    captures = []
    client = None
    start = time.monotonic()
    try:
        client = connect_with_retry(args.port, 20)
        supported = client.send_packet("qSupported:multiprocess+;swbreak+;hwbreak+")
        initial_status = client.send_packet("?")
        client.resume()
        emulation_start = time.monotonic()
        for index, capture_time in enumerate(times):
            remaining = capture_time - (time.monotonic() - emulation_start)
            if remaining > 0:
                time.sleep(remaining)
            stop_reply = client.interrupt()
            registers = client.send_packet("g")
            ram = client.read_memory(RAM_BASE, RAM_SIZE)
            output_dir = project / "work/runtime"
            output_dir.mkdir(parents=True, exist_ok=True)
            ram_path = output_dir / f"ram_{capture_time:05.1f}s.bin"
            ram_path.write_bytes(ram)
            signatures = {}
            for name, needle in (("full_tim", full_tim), ("image_payload", image_payload), ("clut_payload", clut_payload)):
                hits = find_all(ram, needle)
                signatures[name] = {
                    "needle_size": len(needle),
                    "ram_addresses": [f"0x{RAM_BASE + hit:08X}" for hit in hits],
                }
            captures.append({
                "time_seconds": capture_time,
                "stop_reply": stop_reply.decode("ascii", errors="replace"),
                "register_packet_sha256": hashlib.sha256(registers or b"").hexdigest(),
                "ram_path": ram_path.relative_to(project).as_posix(),
                "ram_sha256": hashlib.sha256(ram).hexdigest(),
                "signatures": signatures,
            })
            if index + 1 < len(times):
                client.resume()
    finally:
        if client is not None:
            client.close()
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)

    report = {
        "schema_version": 1,
        "status": "CAPTURED",
        "duckstation_version": "0.1.11636",
        "duckstation_path": str(exe),
        "disc_sha256": expected_disc["bin_sha256"],
        "gdb_server": {
            "host": "127.0.0.1",
            "port": args.port,
            "qSupported": (supported or b"").decode("ascii", errors="replace"),
            "initial_status": (initial_status or b"").decode("ascii", errors="replace"),
        },
        "font_target": {"container_offset": font_offset, "tim_size": len(full_tim), "image_payload_size": len(image_payload), "clut_payload_size": len(clut_payload)},
        "captures": captures,
    }
    report_path = project / "work/analysis/duckstation_ram_trace.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
