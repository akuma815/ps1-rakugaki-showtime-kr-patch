#!/usr/bin/env python3
"""Generate and verify EDC/ECC for raw PlayStation MODE2 Form 1 sectors."""

from __future__ import annotations

import struct

from psx_disc import RAW_SECTOR_SIZE, SYNC


def _make_tables() -> tuple[list[int], list[int], list[int]]:
    edc_lut: list[int] = []
    ecc_f_lut: list[int] = []
    ecc_b_lut = [0] * 256
    for value in range(256):
        edc = value
        for _ in range(8):
            edc = (edc >> 1) ^ (0xD8018001 if edc & 1 else 0)
        edc_lut.append(edc)

        forward = (value << 1) ^ (0x11D if value & 0x80 else 0)
        forward &= 0xFF
        ecc_f_lut.append(forward)
        ecc_b_lut[value ^ forward] = value
    return edc_lut, ecc_f_lut, ecc_b_lut


EDC_LUT, ECC_F_LUT, ECC_B_LUT = _make_tables()


def compute_edc(data: bytes) -> int:
    value = 0
    for byte in data:
        value = (value >> 8) ^ EDC_LUT[(value ^ byte) & 0xFF]
    return value


def _compute_ecc(
    source: bytes,
    major_count: int,
    minor_count: int,
    major_mult: int,
    minor_inc: int,
) -> bytes:
    expected_size = major_count * minor_count
    if len(source) < expected_size:
        raise ValueError(f"ECC source too short: expected {expected_size}, got {len(source)}")
    output = bytearray(major_count * 2)
    for major in range(major_count):
        index = (major >> 1) * major_mult + (major & 1)
        ecc_a = 0
        ecc_b = 0
        for _ in range(minor_count):
            temp = source[index]
            index += minor_inc
            if index >= expected_size:
                index -= expected_size
            ecc_a ^= temp
            ecc_b ^= temp
            ecc_a = ECC_F_LUT[ecc_a]
        ecc_a = ECC_B_LUT[ECC_F_LUT[ecc_a] ^ ecc_b]
        output[major] = ecc_a
        output[major + major_count] = ecc_a ^ ecc_b
    return bytes(output)


def regenerate_mode2_form1(raw_sector: bytes) -> bytes:
    """Return a sector with Form 1 EDC, P parity, and Q parity regenerated."""
    if len(raw_sector) != RAW_SECTOR_SIZE:
        raise ValueError("raw sector must be exactly 2352 bytes")
    if raw_sector[:12] != SYNC or raw_sector[15] != 2:
        raise ValueError("sector is not raw MODE2")
    if raw_sector[16:20] != raw_sector[20:24]:
        raise ValueError("MODE2 subheader copies differ")
    if raw_sector[18] & 0x20:
        raise ValueError("MODE2 Form 2 sector cannot use Form 1 ECC")

    sector = bytearray(raw_sector)
    struct.pack_into("<I", sector, 0x818, compute_edc(bytes(sector[0x10:0x818])))

    saved_header = bytes(sector[0x0C:0x10])
    sector[0x0C:0x10] = b"\x00" * 4
    sector[0x81C:0x8C8] = _compute_ecc(bytes(sector[0x0C:0x81C]), 86, 24, 2, 86)
    sector[0x8C8:0x930] = _compute_ecc(bytes(sector[0x0C:0x8C8]), 52, 43, 86, 88)
    sector[0x0C:0x10] = saved_header
    return bytes(sector)


def verify_mode2_form1(raw_sector: bytes) -> bool:
    return regenerate_mode2_form1(raw_sector) == raw_sector
