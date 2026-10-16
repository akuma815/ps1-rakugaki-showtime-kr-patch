#!/usr/bin/env python3
"""Render Korean onomatopoeia in the game's outlined-scribble style (HITFX-001).

The stock hit words (バキ, ボカ〜ン, ドカ) are whole-word 4bpp bitmaps that use
exactly three palette entries: 0 transparent, 1 black body, 15 white outline.
A plain upright gothic render sits wrong next to them, so each syllable is
rotated a little and nudged off the baseline, the way the hand-drawn originals
lean, and the outline is grown from the body rather than stroked by the font.

Only the three indices above are ever emitted, so the result drops into the
stock cell byte-for-byte with no palette work.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FONT_PATH = Path(r"g:/Downloads/Korean Traslate Project/Font/RiaSans-Bold.ttf")
BODY_INDEX = 1
OUTLINE_INDEX = 15
OUTLINE_RADIUS = 2
SUPERSAMPLE = 4

# Per-syllable lean and vertical nudge, cycled across the word. The stock words
# tilt the same way -- first character kicked back, later ones forward.
TILTS = (10.0, -7.0, 5.0, -9.0)
NUDGES = (1, -2, 1, -1)


def _syllable(char: str, size: int, tilt: float) -> Image.Image:
    """One rotated syllable as an 'L' mask, supersampled so the tilt is clean."""
    font = ImageFont.truetype(str(FONT_PATH), size * SUPERSAMPLE)
    box = font.getbbox(char)
    big = Image.new("L", (box[2] - box[0] + 2, box[3] - box[1] + 2), 0)
    ImageDraw.Draw(big).text((-box[0] + 1, -box[1] + 1), char, font=font, fill=255)
    if tilt:
        big = big.rotate(tilt, resample=Image.BICUBIC, expand=True)
    return big.resize((max(1, big.width // SUPERSAMPLE), max(1, big.height // SUPERSAMPLE)),
                      Image.LANCZOS)


def render_word(text: str, width: int, height: int, *, threshold: int = 110) -> list[list[int]]:
    """Lay the word out across width x height and return a grid of palette indices."""
    inner_w = width - 2 * OUTLINE_RADIUS
    inner_h = height - 2 * OUTLINE_RADIUS
    for size in range(height, 7, -1):
        pieces = [_syllable(ch, size, TILTS[i % len(TILTS)]) for i, ch in enumerate(text)]
        total = sum(p.width for p in pieces) - (len(pieces) - 1) * max(1, size // 8)
        if total <= inner_w and max(p.height for p in pieces) <= inner_h:
            break
    else:
        pieces = [_syllable(ch, 8, TILTS[i % len(TILTS)]) for i, ch in enumerate(text)]
        total = sum(p.width for p in pieces)

    canvas = Image.new("L", (width, height), 0)
    overlap = max(1, size // 8)
    x = max(0, (width - total) // 2)
    for i, piece in enumerate(pieces):
        nudge = NUDGES[i % len(NUDGES)]
        y = (height - piece.height) // 2 + nudge
        canvas.paste(piece, (x, max(0, min(height - piece.height, y))), piece)
        x += piece.width - overlap

    pixels = canvas.load()
    body = [[pixels[x, y] >= threshold for x in range(width)] for y in range(height)]
    grid = [[BODY_INDEX if body[y][x] else 0 for x in range(width)] for y in range(height)]
    radius = range(-OUTLINE_RADIUS, OUTLINE_RADIUS + 1)
    for y in range(height):
        for x in range(width):
            if body[y][x]:
                continue
            if any(0 <= y + dy < height and 0 <= x + dx < width and body[y + dy][x + dx]
                   for dy in radius for dx in radius):
                grid[y][x] = OUTLINE_INDEX
    return grid


def encode_bigfont_4bpp(text: str, width: int, height: int) -> bytes:
    """Render one big-font cell (BIGFONT-KOREAN-001).

    Unlike the hit words, these cells hold a single solid shape: palette 15 on
    transparent, with no outline of their own -- the drop shadow and coloured
    fringe seen on screen are drawn by the game from a second copy of the cell.
    So the cell is a silhouette, and an empty string is a legitimate cell: the
    Korean readings drop the long-vowel mark that half these words carry.
    """
    stride = width // 2
    if not text:
        return bytes(stride * height)
    inner_w = width - 2
    inner_h = height - 2
    pieces = None
    for size in range(height + 4, 7, -1):
        pieces = [_syllable(ch, size, TILTS[i % len(TILTS)]) for i, ch in enumerate(text)]
        if sum(p.width for p in pieces) <= inner_w and max(p.height for p in pieces) <= inner_h:
            break
    canvas = Image.new("L", (width, height), 0)
    total = sum(p.width for p in pieces)
    x = max(0, (width - total) // 2)
    for i, piece in enumerate(pieces):
        y = (height - piece.height) // 2 + NUDGES[i % len(NUDGES)]
        canvas.paste(piece, (x, max(0, min(height - piece.height, y))), piece)
        x += piece.width
    pixels = canvas.load()
    out = bytearray(stride * height)
    for y in range(height):
        for x in range(width):
            if pixels[x, y] < 110:
                continue
            i = y * stride + x // 2
            out[i] = (out[i] & 0x0F) | (OUTLINE_INDEX << 4) if x & 1 else (out[i] & 0xF0) | OUTLINE_INDEX
    return bytes(out)


def encode_marker_4bpp(index: int, width: int, height: int) -> bytes:
    """Stamp a cell with its own index, for the BIGFONT-WORDS-002 diagnostic build.

    Every static search for the word-composition data has failed, so instead of
    decoding it we make the game read it out: with each cell showing its number,
    any word on screen spells its own cell sequence, and the full word list
    falls out of a single playthrough.

    Digits are drawn upright and un-tilted -- this build exists to be read, not
    to look like the game -- and reuse the stock ink values so the cell stays a
    valid 4bpp bitmap.
    """
    grid = [[0] * width for _ in range(height)]
    text = f"{index:02d}"
    inner_w = width - 2 * OUTLINE_RADIUS
    inner_h = height - 2 * OUTLINE_RADIUS
    for size in range(height, 7, -1):
        font = ImageFont.truetype(str(FONT_PATH), size)
        box = font.getbbox(text)
        if box[2] - box[0] <= inner_w and box[3] - box[1] <= inner_h:
            break
    canvas = Image.new("L", (width, height), 0)
    ImageDraw.Draw(canvas).text(((width - (box[2] - box[0])) // 2 - box[0],
                                 (height - (box[3] - box[1])) // 2 - box[1]),
                                text, font=font, fill=255)
    pixels = canvas.load()
    body = [[pixels[x, y] >= 110 for x in range(width)] for y in range(height)]
    radius = range(-OUTLINE_RADIUS, OUTLINE_RADIUS + 1)
    for y in range(height):
        for x in range(width):
            if body[y][x]:
                grid[y][x] = BODY_INDEX
            elif any(0 <= y + dy < height and 0 <= x + dx < width and body[y + dy][x + dx]
                     for dy in radius for dx in radius):
                grid[y][x] = OUTLINE_INDEX
    stride = width // 2
    out = bytearray(stride * height)
    for y in range(height):
        for x in range(width):
            value = grid[y][x]
            if not value:
                continue
            i = y * stride + x // 2
            out[i] = (out[i] & 0x0F) | (value << 4) if x & 1 else (out[i] & 0xF0) | value
    return bytes(out)


def encode_effect_4bpp(text: str, width: int, height: int) -> bytes:
    """Pack render_word output as a raw 4bpp bitmap, low nibble = even x."""
    grid = render_word(text, width, height)
    stride = width // 2
    out = bytearray(stride * height)
    for y in range(height):
        for x in range(width):
            value = grid[y][x]
            if not value:
                continue
            index = y * stride + x // 2
            if x & 1:
                out[index] = (out[index] & 0x0F) | (value << 4)
            else:
                out[index] = (out[index] & 0xF0) | value
    return bytes(out)
