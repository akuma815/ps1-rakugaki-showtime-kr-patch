#!/usr/bin/env python3
"""Count the translation-corpus glyph working set against the 255-cell budget."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import unicodedata
from collections import Counter
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def glyph_class(char: str) -> str:
    codepoint = ord(char)
    if 0xAC00 <= codepoint <= 0xD7A3:
        return "hangul_syllable"
    if 0x1100 <= codepoint <= 0x11FF or 0x3130 <= codepoint <= 0x318F or 0xA960 <= codepoint <= 0xA97F or 0xD7B0 <= codepoint <= 0xD7FF:
        return "hangul_jamo"
    if char.isdigit():
        return "digit"
    if "LATIN" in unicodedata.name(char, ""):
        return "latin"
    return "symbol_or_other"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    project = args.project.resolve()
    csv_path = project / "workspace/text/rakugaki_text_ja_ko.csv"
    policy_path = project / "workspace/config/hangul_glyph_policy.json"
    output = project / "work/analysis/glyph_census_001.json"

    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    budget = int(policy["font_cell_budget"])
    mandatory = set(policy["mandatory_preserved_glyphs"])
    if len(mandatory) != len(policy["mandatory_preserved_glyphs"]):
        raise ValueError("mandatory preserved glyph list contains duplicates")
    forbidden = {"\t", "\n", "\r"}

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 324 or len({row["id"] for row in rows}) != 324:
        raise ValueError("SSOT denominator or stable IDs changed")

    status_counts = Counter(row["status"] for row in rows)
    translated_rows = [row for row in rows if row["status"] in {"translate", "draft", "reviewed", "approved"}]
    excluded_rows = [row for row in rows if row["status"] == "excluded"]
    invalid: list[dict[str, str]] = []
    corpus_glyphs: set[str] = set()
    glyph_occurrences: Counter[str] = Counter()
    for row in translated_rows:
        text = row["translation"]
        if not text.strip():
            invalid.append({"id": row["id"], "reason": "translated status without translation"})
            continue
        if unicodedata.normalize("NFC", text) != text:
            invalid.append({"id": row["id"], "reason": "translation is not NFC normalized"})
        bad = sorted(set(text) & forbidden)
        if bad:
            invalid.append({"id": row["id"], "reason": "forbidden control character"})
        for char in text:
            if char.isspace():
                continue
            corpus_glyphs.add(char)
            glyph_occurrences[char] += 1

    required = mandatory | corpus_glyphs
    minimum_static_banks = math.ceil(len(required) / budget) if required else 0
    class_counts = Counter(glyph_class(char) for char in required)
    coverage_complete = len(translated_rows) + len(excluded_rows) == len(rows) and not invalid
    if invalid:
        status = "INVALID_TRANSLATION_INPUT"
    elif not translated_rows:
        status = "WAITING_FOR_KOREAN_TRANSLATION"
    elif not coverage_complete:
        status = "PARTIAL_TRANSLATION_PROVISIONAL_COUNT_ONLY"
    elif len(required) <= budget:
        status = "FIXED_ATLAS_CAPACITY_ELIGIBLE_PENDING_POC"
    else:
        status = "FIXED_ATLAS_OVERFLOW_TWO_BANK_POC_REQUIRED"

    decision_reason = {
        "FIXED_ATLAS_CAPACITY_ELIGIBLE_PENDING_POC": "The complete corpus fits the 255-cell budget; a decoder-remap runtime PoC is still required.",
        "FIXED_ATLAS_OVERFLOW_TWO_BANK_POC_REQUIRED": "The complete corpus exceeds the 255-cell budget; proceed to the static two-bank hook PoC.",
    }.get(status, "A final capacity decision requires every SSOT row to be translated or explicitly excluded.")
    report = {
        "schema_version": 1,
        "observation_id": "GLYPH-CENSUS-001",
        "status": status,
        "ssot": {
            "path": "workspace/text/rakugaki_text_ja_ko.csv",
            "sha256": sha256(csv_path),
            "rows": len(rows),
            "status_counts": dict(sorted(status_counts.items())),
            "translated_rows": len(translated_rows),
            "excluded_rows": len(excluded_rows),
            "coverage_complete": coverage_complete,
        },
        "policy": {
            "path": "workspace/config/hangul_glyph_policy.json",
            "sha256": sha256(policy_path),
            "status": policy["status"],
            "cell_budget": budget,
            "blank_fallback_cell": policy["blank_fallback_cell"],
            "mandatory_preserved_glyphs": "".join(sorted(mandatory)),
            "mandatory_preserved_glyph_count": len(mandatory),
        },
        "census": {
            "unique_translation_glyphs": len(corpus_glyphs),
            "unique_required_glyphs": len(required),
            "remaining_cells": budget - len(required),
            "minimum_static_banks": minimum_static_banks,
            "minimum_static_bank_capacity": minimum_static_banks * budget,
            "minimum_static_bank_headroom": minimum_static_banks * budget - len(required),
            "class_counts": dict(sorted(class_counts.items())),
            "required_glyphs": "".join(sorted(required)),
            "translation_glyph_occurrences": [
                {"glyph": glyph, "count": count, "class": glyph_class(glyph)}
                for glyph, count in sorted(glyph_occurrences.items())
            ],
        },
        "invalid_rows": invalid,
        "decision": {
            "fixed_atlas_selected": status == "FIXED_ATLAS_CAPACITY_ELIGIBLE_PENDING_POC",
            "two_bank_poc_required": status == "FIXED_ATLAS_OVERFLOW_TWO_BANK_POC_REQUIRED",
            "reason": decision_reason,
        },
        "next_gate": "build the static two-bank hook PoC" if status == "FIXED_ATLAS_OVERFLOW_TWO_BANK_POC_REQUIRED" else "complete translation coverage and rerun the census",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"status={status} translated={len(translated_rows)}/{len(rows)} required={len(required)}/{budget}")
    if invalid:
        raise ValueError(f"invalid translation rows: {len(invalid)}")


if __name__ == "__main__":
    main()
