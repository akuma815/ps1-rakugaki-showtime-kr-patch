#!/usr/bin/env python3
"""Validate and decode standard PlayStation TIM images."""

from __future__ import annotations

import struct

from PIL import Image


MAGIC = b"\x10\x00\x00\x00"


def _parse_block(data: bytes, offset: int) -> tuple[dict, int] | None:
    if offset + 12 > len(data):
        return None
    size = struct.unpack_from("<I", data, offset)[0]
    if size < 12 or offset + size > len(data):
        return None
    x, y, width_words, height = struct.unpack_from("<4H", data, offset + 4)
    payload_size = size - 12
    if not width_words or not height or payload_size != width_words * height * 2:
        return None
    return ({
        "size": size,
        "x": x,
        "y": y,
        "width_words": width_words,
        "height": height,
        "payload_offset": offset + 12,
        "payload_size": payload_size,
    }, offset + size)


def parse_tim(data: bytes, offset: int) -> dict | None:
    if data[offset : offset + 4] != MAGIC or offset + 8 > len(data):
        return None
    flags = struct.unpack_from("<I", data, offset + 4)[0]
    if flags & ~0x0F:
        return None
    bpp_code = flags & 7
    if bpp_code > 3:
        return None
    cursor = offset + 8
    clut = None
    if flags & 8:
        parsed = _parse_block(data, cursor)
        if parsed is None:
            return None
        clut, cursor = parsed
    parsed = _parse_block(data, cursor)
    if parsed is None:
        return None
    image, end = parsed
    if bpp_code == 0:
        pixel_width, min_colors = image["width_words"] * 4, 16
    elif bpp_code == 1:
        pixel_width, min_colors = image["width_words"] * 2, 256
    elif bpp_code == 2:
        pixel_width, min_colors = image["width_words"], 0
    else:
        row_bytes = image["width_words"] * 2
        if row_bytes % 3:
            return None
        pixel_width, min_colors = row_bytes // 3, 0
    if not pixel_width or pixel_width > 1024 or image["height"] > 512:
        return None
    if bpp_code in (0, 1):
        if clut is None or clut["width_words"] * clut["height"] < min_colors:
            return None
    return {
        "offset": offset,
        "flags": flags,
        "bpp": (4, 8, 16, 24)[bpp_code],
        "has_clut": clut is not None,
        "clut": clut,
        "image": image,
        "pixel_width": pixel_width,
        "pixel_height": image["height"],
        "total_size": end - offset,
    }


def scan_tims(data: bytes) -> list[dict]:
    records: list[dict] = []
    cursor = 0
    while True:
        offset = data.find(MAGIC, cursor)
        if offset < 0:
            return records
        tim = parse_tim(data, offset)
        if tim is not None:
            records.append(tim)
        cursor = offset + 1


def _psx_color(value: int) -> tuple[int, int, int, int]:
    red = (value & 0x1F) * 255 // 31
    green = ((value >> 5) & 0x1F) * 255 // 31
    blue = ((value >> 10) & 0x1F) * 255 // 31
    return red, green, blue, 0 if value == 0 else 255


def decode_tim(data: bytes, tim: dict, palette_index: int = 0) -> Image.Image:
    """Decode a validated TIM, selecting one CLUT row for indexed images."""
    image_meta = tim["image"]
    payload = data[
        image_meta["payload_offset"] : image_meta["payload_offset"] + image_meta["payload_size"]
    ]
    width, height = tim["pixel_width"], tim["pixel_height"]
    palette: list[tuple[int, int, int, int]] = []
    if tim["clut"] is not None:
        clut = tim["clut"]
        clut_data = data[clut["payload_offset"] : clut["payload_offset"] + clut["payload_size"]]
        palette = [_psx_color(struct.unpack_from("<H", clut_data, offset)[0]) for offset in range(0, len(clut_data), 2)]

    pixels: list[tuple[int, int, int, int]] = []
    if tim["bpp"] == 4:
        palette_start = palette_index * 16
        active_palette = palette[palette_start : palette_start + 16]
        if len(active_palette) != 16:
            raise ValueError(f"4bpp palette index out of range: {palette_index}")
        for value in payload:
            pixels.extend((active_palette[value & 15], active_palette[value >> 4]))
    elif tim["bpp"] == 8:
        palette_start = palette_index * 256
        active_palette = palette[palette_start : palette_start + 256]
        if len(active_palette) != 256:
            raise ValueError(f"8bpp palette index out of range: {palette_index}")
        pixels = [active_palette[value] for value in payload]
    elif tim["bpp"] == 16:
        pixels = [_psx_color(struct.unpack_from("<H", payload, offset)[0]) for offset in range(0, len(payload), 2)]
    else:
        row_size = image_meta["width_words"] * 2
        for row in range(height):
            row_data = payload[row * row_size : (row + 1) * row_size]
            for offset in range(0, len(row_data), 3):
                blue, green, red = row_data[offset : offset + 3]
                pixels.append((red, green, blue, 255))
    result = Image.new("RGBA", (width, height))
    result.putdata(pixels[: width * height])
    return result
