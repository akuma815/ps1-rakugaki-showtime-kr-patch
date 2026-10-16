#!/usr/bin/env python3
"""Render Korean menu-label strips to match the stock 4bpp bitmap dimensions.

Each stock label is a fixed-width strip (stride*2 px x 16 rows). We render the
Korean text with RiaSans-Bold, fit it to the strip width, and quantize to
the same 4-level ink the originals use, so it can be patched in place with
Expected Writes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FONT_PATH = Path(__file__).resolve().parents[2] / "assets/fonts/RiaSans-Bold.ttf"
STRIP_HEIGHT = 16


SMALL_FONT_PATH = Path(__file__).resolve().parents[2] / "assets/fonts/Galmuri9.ttf"
SMALL_FONT_SIZE = 8


def render_text_strip(text: str, width_px: int, *, height: int = STRIP_HEIGHT,
                      max_size: int = 16, min_size: int = 9) -> Image.Image:
    """Render `text` centered in a width_px x height 1-bit-ish grayscale strip.

    RiaSans-Bold is far too heavy below ~12 px, where a Korean syllable
    with a final consonant turns into a blob, so short boxes switch to the
    selected small font rendered directly without resampling.
    """
    if height < 12:
        if SMALL_FONT_PATH.name.lower() == "galmuri8x8.ttf":
            # This bitmap-only TTF exposes its native 8-row glyphs at 16 ppem.
            # Latin glyphs occupy a half-width 4x8 cell (3 ink pixels + spacing).
            # Widen to 5x8 with nearest-neighbour so lowercase m stays distinct
            # from n at the game's display scale; both target boxes have room.
            font = ImageFont.truetype(str(SMALL_FONT_PATH), 16)
            native = Image.new("L", (5 * len(text), 8), 0)
            for index, ch in enumerate(text):
                bbox = font.getbbox(ch)
                glyph = Image.new("L", (16, 16), 0)
                ImageDraw.Draw(glyph).text((-bbox[0], -bbox[1]), ch, font=font, fill=255)
                half_width = glyph.crop((0, 0, 4, 8))
                widened = half_width.resize((5, 8), Image.Resampling.NEAREST)
                native.paste(widened, (5 * index, 0))
        else:
            font = ImageFont.truetype(str(SMALL_FONT_PATH), min(SMALL_FONT_SIZE, height))
            bbox = font.getbbox(text)
            native_width = max(round(font.getlength(text)), bbox[2] - bbox[0])
            native_height = bbox[3] - bbox[1]
            native = Image.new("L", (native_width, native_height), 0)
            ImageDraw.Draw(native).text((-bbox[0], -bbox[1]), text, font=font, fill=255)
        image = Image.new("L", (width_px, height), 0)
        image.paste(native, (max(0, (width_px - native.width) // 2),
                             max(0, (height - native.height) // 2)))
        return image
    for size in range(max_size, min_size - 1, -1):
        font = ImageFont.truetype(str(FONT_PATH), size)
        # measure the whole string
        ascent, descent = font.getmetrics()
        widths = []
        for ch in text:
            bbox = font.getbbox(ch)
            widths.append(bbox[2] - bbox[0] + 1)
        total = sum(widths)
        if total <= width_px and size <= height + 2:
            image = Image.new("L", (width_px, height), 0)
            draw = ImageDraw.Draw(image)
            x = max(0, (width_px - total) // 2)
            # vertical centering from the tallest glyph
            top = min(font.getbbox(ch)[1] for ch in text)
            bottom = max(font.getbbox(ch)[3] for ch in text)
            y = (height - (bottom - top)) // 2 - top
            for ch, w in zip(text, widths):
                bbox = font.getbbox(ch)
                draw.text((x - bbox[0], y), ch, font=font, fill=255)
                x += w
            return image
    # fallback: squeeze the smallest size and let PIL clip
    font = ImageFont.truetype(str(FONT_PATH), min_size)
    image = Image.new("L", (width_px, height), 0)
    ImageDraw.Draw(image).text((1, 1), text, font=font, fill=255)
    return image


def quantize_4level(image: Image.Image, threshold: int) -> list[list[int]]:
    """Map to the stock ink levels: 0 (bg) and 0xF (ink), 4bpp style."""
    pixels = image.load()
    return [[0xF if pixels[x, y] >= threshold else 0 for x in range(image.width)]
            for y in range(image.height)]


TRANSLATIONS = {
    "デモンストレーション": "데모 플레이",
    "ソウサセツメイ": "조작 설명",
    "キーコンフィグ": "키 설정",
    "ゲームセッテイ": "게임 설정",
    "プレイヤーアップ": "플레이어 업",
    "ミュージック": "음악",
    "スタッフ": "스태프",
    "フィールド": "필드",
    "ノーマル": "노멀",
    "90°": "90°",
    "45°": "45°",
    "ユーザーフレンドリー": "유저 프렌들리",
    "バイブレーション": "진동",
    "セレクト": "셀렉트",
    "テスト": "테스트",
    "カメラ": "카메라",
    "キッズ": "키즈",
    "キャラクター": "캐릭터",
    "モード": "모드",
    "オーケー？": "오케이?",
    "オプション": "옵션",
    "バーサス": "버서스",
    "オート": "오토",
    "ムービー": "무비",
    "オン": "온",
    "マーカー": "마커",
    "ゲームスピード": "게임 스피드",
    "トレジャー": "트레저",
    "サウンド": "사운드",
    "ロール": "롤",
    "ステレオ": "스테레오",
    "ボリューム": "볼륨",
    "エフェクト": "이펙트",
    "トレーニング": "트레이닝",
    "オフ": "오프",
    "データ": "데이터",
    "セーブ": "세이브",
    "モノラル": "모노",
    "ミドル": "미들",
    "ロード": "로드",
    "ポーズ": "포즈",
    "ステージ": "스테이지",
    "バーサスリスタート": "버서스 리스타트",
    "オタノシミ": "즐길거리",
    "クレジット": "크레딧",
    "スペシャル": "스페셜",
    "ヒョウジ": "표시",
    "トレジャー(스태프롤)": "트레저",
    "ラクガキショータイム": "라쿠가키 쇼타임",
}


def encode_strip_4bpp(text: str, stride_bytes: int, threshold: int = 110,
                      *, height: int = STRIP_HEIGHT) -> bytes:
    """Render `text` and pack it as a stride_bytes x height raw 4bpp bitmap.

    Pixel (x,y) -> nibble in byte (y*stride + x//2): low nibble is even x,
    high nibble is odd x, matching the stock strip layout.
    """
    width_px = stride_bytes * 2
    strip = render_text_strip(text, width_px, height=height)
    grid = quantize_4level(strip, threshold)
    out = bytearray(stride_bytes * height)
    for y in range(height):
        for x in range(width_px):
            value = grid[y][x]
            if not value:
                continue
            index = y * stride_bytes + x // 2
            if x & 1:
                out[index] = (out[index] & 0x0F) | (value << 4)
            else:
                out[index] = (out[index] & 0xF0) | value
    return bytes(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--threshold", type=int, default=110)
    parser.add_argument("--sample", action="store_true", help="render the sample comparison sheet only")
    args = parser.parse_args()
    project = args.project.resolve()
    catalog = json.loads((project / "work/analysis/menu_strips_catalog.json").read_text(encoding="utf-8"))

    translations = {
        "デモンストレーション": "데모",
        "ソウサセツメイ": "조작 설명",
        "キーコンフィグ": "키 설정",
        "ゲームセッテイ": "게임 설정",
        "プレイヤーアップ": "플레이어 업",
        "ミュージック": "음악",
        "スタッフ": "스태프",
        "フィールド": "필드",
        "ノーマル": "노멀",
        "90°": "90°",
        "45°": "45°",
        "ユーザーフレンドリー": "유저 프렌들리",
        "バイブレーション": "진동",
        "セレクト": "셀렉트",
        "テスト": "테스트",
        "カメラ": "카메라",
        "キッズ": "키즈",
    }

    hagecho = (project / "extracted/HAGECHO.BIN").read_bytes()
    out_dir = project / "work/graphics/menu_korean_samples"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for entry in catalog["entries"]:
        label = entry.get("label")
        korean = translations.get(label)
        if korean is None:
            continue
        width_px = entry["width_px"]
        off = int(entry["disc_offset"], 16)
        stride = entry["stride_bytes"]
        # original strip
        orig = Image.new("L", (width_px, STRIP_HEIGHT), 0)
        op = orig.load()
        for y in range(STRIP_HEIGHT):
            for i in range(stride):
                b = hagecho[off + y * stride + i]
                op[i * 2, y] = (b & 0xF) * 17
                op[i * 2 + 1, y] = ((b >> 4) & 0xF) * 17
        # korean render
        strip = render_text_strip(korean, width_px)
        rows.append((label, korean, width_px, orig, strip))

    scale = 3
    pad = 8
    sheet_w = 2 * max(r[2] for r in rows) * scale + 60
    sheet_h = len(rows) * (STRIP_HEIGHT * scale + pad + 14)
    sheet = Image.new("RGB", (sheet_w, sheet_h), (25, 25, 25))
    draw = ImageDraw.Draw(sheet)
    y = 0
    for label, korean, width_px, orig, strip in rows:
        draw.text((4, y), f"{label} -> {korean} ({width_px}px)", fill=(255, 255, 0))
        y += 14
        sheet.paste(orig.convert("RGB").resize((width_px * scale, STRIP_HEIGHT * scale), Image.NEAREST), (4, y))
        sheet.paste(strip.convert("RGB").resize((width_px * scale, STRIP_HEIGHT * scale), Image.NEAREST),
                    (max(r[2] for r in rows) * scale + 30, y))
        y += STRIP_HEIGHT * scale + pad
    sample_path = out_dir / "sample_compare.png"
    sheet.save(sample_path)
    print(f"sample sheet: {sample_path.relative_to(project).as_posix()} ({len(rows)} labels)")


if __name__ == "__main__":
    main()
