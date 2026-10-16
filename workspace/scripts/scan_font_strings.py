#!/usr/bin/env python3
"""Search core files for strings consumed by the confirmed Shift-JIS decoder."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ANCHORS = [
    "オプション", "ノーマル", "バーサス", "トレーニング",
    "モードセレクト", "ユーザーフレンドリー", "サウンド",
    "デモンストレーション", "スタッフ", "データ", "キーコンフィグ",
    "プッシュスタートボタン",
]


def encode(text: str) -> bytes:
    return text.encode("cp932")


def standard_sjis_cell(code: int) -> int:
    if code == 0x815B:
        return 0xFD
    if 0x824F <= code < 0x8259:
        return code - 0x824F
    if 0x8260 <= code < 0x827A:
        return code - 0x824F
    if 0x8281 <= code < 0x829B:
        return code - 0x8250
    if 0x829F <= code < 0x82F2:
        return code - 0x824F
    if 0x8340 <= code < 0x837F:
        return code - 0x829D
    if 0x8380 <= code < 0x8397:
        return code - 0x829E
    raise ValueError(f"character is outside the decoder's standard ranges: 0x{code:04X}")


def cell_codes(text: str) -> list[int]:
    output = []
    for char in text:
        raw = char.encode("cp932")
        if len(raw) != 2:
            raise ValueError(f"game reader requires a two-byte code for {char!r}")
        output.append(standard_sjis_cell((raw[0] << 8) | raw[1]))
    return output


KATAKANA = {
    char: cell_codes(char)[0]
    for char in "ァアィイゥウェエォオカガキギクグケゲコゴサザシジスズセゼソゾタダチッツヅテデトドナニヌネノハバパヒビピフブプヘベペホボポマミムメモャヤュユョヨラリルレロヮワヲンー"
}


def find_all(data: bytes, needle: bytes) -> list[int]:
    hits = []
    cursor = 0
    while True:
        offset = data.find(needle, cursor)
        if offset < 0:
            return hits
        hits.append(offset)
        cursor = offset + 1


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    project = args.project.resolve()
    files = ["SLPM_862.72", "ARA.BIN", "DO.BIN", "HAGECHO.BIN"]
    block_report = json.loads((project / "work/analysis/hagecho_blocks.json").read_text(encoding="utf-8"))
    blocks = block_report["blocks"]
    results = []
    for filename in files:
        data = (project / "extracted" / filename).read_bytes()
        for text in ANCHORS:
            encoded = encode(text)
            for offset in find_all(data, encoded):
                record = {"file": filename, "offset": offset, "offset_hex": f"0x{offset:X}", "text": text, "encoded_hex": encoded.hex()}
                if filename == "HAGECHO.BIN":
                    block = next((item for item in blocks if item["start"] <= offset < item["end"]), None)
                    if block is not None:
                        record["block_index"] = block["index"]
                        record["block_offset"] = offset - block["start"]
                results.append(record)
    report = {
        "schema_version": 1,
        "storage": "NUL-terminated two-byte Shift-JIS strings; runtime reads lead/trail big-endian and maps through 0x800430DC",
        "anchors": [{"text": text, "encoded_hex": encode(text).hex(), "cell_codes_hex": " ".join(f"{value:02X}" for value in cell_codes(text))} for text in ANCHORS],
        "hit_count": len(results),
        "hits": results,
        "status": "RUNTIME_CONSUMER_CONFIRMED_STATIC_ANCHORS",
    }
    output = project / "work/analysis/font_string_anchor_hits.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
