#!/usr/bin/env python3
"""Audit completed Korean translations without changing user-authored text."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path


KANA_RE = re.compile(r"[\u3040-\u30FF\u31F0-\u31FF]")
CJK_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF]")
RAW_PLACEHOLDER_RE = re.compile(r"<[0-9A-Fa-f]{4}>")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    project = args.project.resolve()
    csv_path = project / "workspace/text/rakugaki_text_ja_ko.csv"
    manifest_path = project / "workspace/text/rakugaki_text_manifest.json"
    output = project / "work/analysis/korean_translation_audit_001.json"
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cp932_by_id = {entry["id"]: entry["cp932_text"] for entry in manifest["entries"]}

    flags: list[dict[str, object]] = []
    flag_counts: Counter[str] = Counter()
    for row_number, row in enumerate(rows, start=2):
        text = row["translation"]
        categories: list[str] = []
        if not text.strip():
            categories.append("empty_translation")
        if text == row["original_text"]:
            categories.append("unchanged_from_original")
        if KANA_RE.search(text):
            categories.append("kana_remaining")
        if CJK_RE.search(text):
            categories.append("cjk_ideograph_remaining")
        if RAW_PLACEHOLDER_RE.search(text):
            categories.append("raw_decoder_placeholder_remaining")
        if text != text.strip():
            categories.append("edge_whitespace")
        if categories:
            flag_counts.update(categories)
            flags.append(
                {
                    "row": row_number,
                    "id": row["id"],
                    "categories": categories,
                    "original_text": row["original_text"],
                    "cp932_reference": cp932_by_id[row["id"]],
                    "translation": text,
                    "notes": row["notes"],
                }
            )

    blocking = [
        flag for flag in flags
        if {"empty_translation", "raw_decoder_placeholder_remaining"} & set(flag["categories"])
    ]
    report = {
        "schema_version": 1,
        "observation_id": "TRANSLATION-AUDIT-001",
        "status": "COMPLETE_UNREVIEWED_WITH_BLOCKING_FLAGS" if blocking else "COMPLETE_UNREVIEWED_WITH_REVIEW_FLAGS",
        "ssot_sha256": sha256(csv_path),
        "rows": len(rows),
        "nonempty_translations": sum(bool(row["translation"].strip()) for row in rows),
        "status_counts": dict(sorted(Counter(row["status"] for row in rows).items())),
        "flag_counts": dict(sorted(flag_counts.items())),
        "flagged_rows": len(flags),
        "blocking_rows": len(blocking),
        "blocking_ids": [flag["id"] for flag in blocking],
        "flags": flags,
        "interpretation": {
            "unchanged_from_original": "May be intentional for English labels, player numbers, separators, and abbreviations; requires human review rather than automatic replacement.",
            "kana_remaining": "May be a separator or name-style mark, but mixed Korean/Japanese text should be reviewed.",
            "raw_decoder_placeholder_remaining": "Must be corrected from the CP932 reference before a full translation build.",
            "edge_whitespace": "May be intentional screen alignment; preserve until the owning layout is runtime-checked.",
        },
        "automatic_text_changes": False,
        "next_gate": (
            "resolve or explicitly approve blocking translation flags before implementation"
            if blocking
            else "build the static two-bank decoder/primitive-hook PoC while retaining nonblocking flags for human QA"
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"status={report['status']} rows={len(rows)} flagged={len(flags)} blocking={len(blocking)}")


if __name__ == "__main__":
    main()
