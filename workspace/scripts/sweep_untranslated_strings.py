#!/usr/bin/env python3
"""Sweep both containers for Shift-JIS strings the translation CSV does not cover.

Hunting one reported word at a time kept finding real gaps (リスタート？, item
names, taunt lines) that the earlier anchor-based scan had missed, which means
the scan region -- not the corpus -- was the limit. This walks every byte of ARA
and HAGECHO instead and reports what the CSV has never seen.

A hit must be NUL-terminated and built only from the character classes the game
actually uses, because HAGECHO is mostly pixel data and random bytes decode as
Shift-JIS kanji all day long.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Kana, the fullwidth ASCII block, and the handful of punctuation marks the game
# uses. Kanji are deliberately excluded: this game's text is kana-only, so any
# kanji run is noise from pixel data.
ALLOWED = re.compile(
    r"^[぀-ゟ゠-ヿ　・！-～’”°]+$"
)
MIN_CHARS = 2


def scan(blob: bytes, min_chars: int) -> list[tuple[int, bytes, str]]:
    """Yield (offset, raw, text) for every NUL-terminated kana run."""
    found = []
    i = 0
    size = len(blob)
    while i < size:
        end = blob.find(b"\x00", i)
        if end < 0:
            break
        raw = blob[i:end]
        if len(raw) >= min_chars * 2:
            try:
                text = raw.decode("shift_jis")
            except UnicodeDecodeError:
                text = None
            if text and len(text) >= min_chars and ALLOWED.match(text):
                found.append((i, raw, text))
        i = end + 1
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--min-chars", type=int, default=MIN_CHARS)
    parser.add_argument("--out", default="work/analysis/untranslated_sweep.json")
    args = parser.parse_args()
    project = args.project.resolve()

    csv_path = project / "workspace/text/rakugaki_text_ja_ko.csv"
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    covered_text = {r["original_text"] for r in rows}
    covered_hex = {r["original_hex"].upper().replace(" ", "") for r in rows}

    report = {"schema_version": 1, "observation_id": "STRING-SWEEP-001", "containers": {}}
    for name in ("ARA", "HAGECHO"):
        blob = (project / f"extracted/{name}.BIN").read_bytes()
        hits = scan(blob, args.min_chars)
        misses = []
        for offset, raw, text in hits:
            if text in covered_text or raw.hex().upper() in covered_hex:
                continue
            misses.append({"offset": f"0x{offset:06X}", "chars": len(text),
                           "hex": raw.hex().upper(), "text": text})
        report["containers"][name] = {
            "scanned_bytes": len(blob),
            "kana_strings": len(hits),
            "uncovered": len(misses),
            "entries": misses,
        }
        print(f"{name}: {len(hits)} kana strings, {len(misses)} not in CSV")

    out = project / args.out
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out.relative_to(project).as_posix()}")


if __name__ == "__main__":
    main()
