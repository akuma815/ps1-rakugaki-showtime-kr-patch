#!/usr/bin/env python3
"""Strict reader for an ISO 9660 filesystem in a raw PS1 MODE2/2352 track."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator


RAW_SECTOR_SIZE = 2352
SYNC = b"\x00" + b"\xff" * 10 + b"\x00"
FORM1_DATA_OFFSET = 24
FORM1_DATA_SIZE = 2048
FORM2_DATA_SIZE = 2324


@dataclass(frozen=True)
class IsoEntry:
    path: str
    lba: int
    size: int
    is_dir: bool


def _bcd(value: int) -> int:
    return ((value // 10) << 4) | (value % 10)


def expected_msf(lba: int) -> bytes:
    absolute = lba + 150
    minute, remainder = divmod(absolute, 75 * 60)
    second, frame = divmod(remainder, 75)
    return bytes((_bcd(minute), _bcd(second), _bcd(frame)))


def read_raw_sector(track: BinaryIO, lba: int) -> bytes:
    track.seek(lba * RAW_SECTOR_SIZE)
    raw = track.read(RAW_SECTOR_SIZE)
    if len(raw) != RAW_SECTOR_SIZE:
        raise ValueError(f"short sector read at LBA {lba}")
    if raw[:12] != SYNC:
        raise ValueError(f"invalid sync at LBA {lba}")
    if raw[12:15] != expected_msf(lba):
        raise ValueError(f"invalid MSF at LBA {lba}")
    if raw[15] != 2:
        raise ValueError(f"expected MODE2 at LBA {lba}, got mode {raw[15]}")
    if raw[16:20] != raw[20:24]:
        raise ValueError(f"MODE2 subheader copies differ at LBA {lba}")
    return raw


def sector_payload(raw: bytes) -> tuple[bytes, str]:
    if len(raw) != RAW_SECTOR_SIZE:
        raise ValueError("raw sector must be exactly 2352 bytes")
    if raw[18] & 0x20:
        return raw[FORM1_DATA_OFFSET : FORM1_DATA_OFFSET + FORM2_DATA_SIZE], "form2"
    return raw[FORM1_DATA_OFFSET : FORM1_DATA_OFFSET + FORM1_DATA_SIZE], "form1"


def read_extent(track: BinaryIO, lba: int, size: int) -> tuple[bytes, set[str], list[int]]:
    chunks: list[bytes] = []
    forms: set[str] = set()
    lbas: list[int] = []
    remaining = size
    current = lba
    while remaining:
        payload, form = sector_payload(read_raw_sector(track, current))
        take = min(remaining, len(payload))
        chunks.append(payload[:take])
        forms.add(form)
        lbas.append(current)
        remaining -= take
        current += 1
    return b"".join(chunks), forms, lbas


def _parse_record(record: bytes) -> IsoEntry:
    if len(record) < 34:
        raise ValueError("short ISO 9660 directory record")
    lba_le, lba_be = struct.unpack_from("<I", record, 2)[0], struct.unpack_from(">I", record, 6)[0]
    size_le, size_be = struct.unpack_from("<I", record, 10)[0], struct.unpack_from(">I", record, 14)[0]
    if lba_le != lba_be or size_le != size_be:
        raise ValueError("ISO 9660 both-endian fields disagree")
    name_len = record[32]
    name_raw = record[33 : 33 + name_len]
    if name_raw == b"\x00":
        name = "."
    elif name_raw == b"\x01":
        name = ".."
    else:
        name = name_raw.decode("ascii", errors="strict").split(";", 1)[0]
    return IsoEntry(name, lba_le, size_le, bool(record[25] & 0x02))


def _directory_records(data: bytes) -> Iterator[IsoEntry]:
    offset = 0
    while offset < len(data):
        length = data[offset]
        if length == 0:
            offset = ((offset // FORM1_DATA_SIZE) + 1) * FORM1_DATA_SIZE
            continue
        record = data[offset : offset + length]
        if len(record) != length:
            raise ValueError("truncated ISO 9660 directory record")
        yield _parse_record(record)
        offset += length


def inspect_iso(image: Path, hash_files: bool = True) -> dict:
    entries: list[dict] = []
    with image.open("rb") as track:
        pvd_payload, pvd_form = sector_payload(read_raw_sector(track, 16))
        if pvd_form != "form1" or pvd_payload[0] != 1 or pvd_payload[1:6] != b"CD001":
            raise ValueError("ISO 9660 primary volume descriptor not found at LBA 16")
        volume_id = pvd_payload[40:72].decode("ascii", errors="replace").rstrip()
        root_length = pvd_payload[156]
        root = _parse_record(pvd_payload[156 : 156 + root_length])

        def walk(directory: IsoEntry, parent: str) -> None:
            directory_data, forms, _ = read_extent(track, directory.lba, directory.size)
            if forms != {"form1"}:
                raise ValueError(f"directory {parent or '/'} is not entirely Form 1")
            for child in _directory_records(directory_data):
                if child.path in {".", ".."}:
                    continue
                path = f"{parent}/{child.path}" if parent else child.path
                item = {"path": path, "lba": child.lba, "size": child.size, "is_dir": child.is_dir}
                if not child.is_dir:
                    data, child_forms, lbas = read_extent(track, child.lba, child.size)
                    item["sector_forms"] = sorted(child_forms)
                    item["last_lba"] = lbas[-1]
                    if hash_files:
                        item["sha256"] = hashlib.sha256(data).hexdigest()
                entries.append(item)
                if child.is_dir:
                    walk(child, path)

        walk(root, "")
    return {
        "image": image.name,
        "image_size": image.stat().st_size,
        "sector_count": image.stat().st_size // RAW_SECTOR_SIZE,
        "volume_id": volume_id,
        "entries": entries,
    }


def extract_files(image: Path, entries: list[dict], names: set[str], output_dir: Path) -> list[dict]:
    entry_map = {item["path"].upper(): item for item in entries if not item["is_dir"]}
    wanted = {name.replace("\\", "/").upper() for name in names}
    missing = sorted(wanted - entry_map.keys())
    if missing:
        raise ValueError(f"disc paths not found: {', '.join(missing)}")
    extracted: list[dict] = []
    with image.open("rb") as track:
        for key in sorted(wanted):
            entry = entry_map[key]
            data, forms, _ = read_extent(track, entry["lba"], entry["size"])
            destination = output_dir / Path(entry["path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            extracted.append({
                "path": entry["path"],
                "output": destination.as_posix(),
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "sector_forms": sorted(forms),
            })
    return extracted
