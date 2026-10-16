#!/usr/bin/env python3
"""Export and validate the consumer-confirmed dynamic-text translation SSOT."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator


REPORTS = [
    "work/analysis/text_callsites_block1_001.json",
    "work/analysis/text_callsites_block10_001.json",
    "work/analysis/text_callsites_ara_001.json",
    "work/analysis/text_wrapper_callers_001.json",
    "work/analysis/text_wrapper_producers_001.json",
    "work/analysis/text_array_callers_001.json",
    "work/analysis/text_reader_audit_001.json",
]
FIELDNAMES = [
    "id",
    "source",
    "file_offset_hex",
    "block_index",
    "original_hex",
    "original_text",
    "translation",
    "status",
    "notes",
]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def walk(value: Any, path: str = "$") -> Iterator[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, f"{path}[{index}]")


def source_offset(item: dict[str, Any], raw: bytes, ara: bytes, hagecho: bytes) -> tuple[str, int]:
    offsets: list[int] = []
    for key in ("string_file_offset", "ara_offset", "file_offset", "offset"):
        value = item.get(key)
        if isinstance(value, int) and value not in offsets:
            offsets.append(value)
    if not offsets:
        raise ValueError(f"source entry lacks a file offset: {item}")

    matches: list[tuple[str, int]] = []
    explicit = item.get("source")
    for offset in offsets:
        if 0 <= offset <= len(ara) - len(raw) and ara[offset : offset + len(raw)] == raw:
            matches.append(("ARA.BIN", offset))
        if 0 <= offset <= len(hagecho) - len(raw) and hagecho[offset : offset + len(raw)] == raw:
            matches.append(("HAGECHO.BIN", offset))
    matches = list(dict.fromkeys(matches))
    if explicit in ("ARA.BIN", "HAGECHO.BIN"):
        explicit_matches = [match for match in matches if match[0] == explicit]
        if len(explicit_matches) == 1:
            return explicit_matches[0]
    if len(matches) != 1:
        raise ValueError(f"source entry has {len(matches)} byte matches: offsets={offsets} text={item.get('text')!r}")
    return matches[0]


def block_for_offset(blocks: list[dict[str, Any]], offset: int) -> int | None:
    for block in blocks:
        if block["start"] <= offset < block["end"]:
            return int(block["index"])
    return None


def collect(project: Path) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, str]]]]:
    ara = (project / "extracted/ARA.BIN").read_bytes()
    hagecho = (project / "extracted/HAGECHO.BIN").read_bytes()
    blocks = json.loads((project / "work/analysis/hagecho_blocks.json").read_text(encoding="utf-8"))["blocks"]
    entries: dict[tuple[str, int], dict[str, Any]] = {}
    references: dict[tuple[str, int], list[dict[str, str]]] = defaultdict(list)

    for report_name in REPORTS:
        report = json.loads((project / report_name).read_text(encoding="utf-8"))
        for json_path, item in walk(report):
            if not isinstance(item.get("text"), str) or not isinstance(item.get("raw_hex"), str):
                continue
            raw_hex = item["raw_hex"].replace(" ", "")
            try:
                raw = bytes.fromhex(raw_hex)
            except ValueError as exc:
                raise ValueError(f"bad raw_hex at {report_name}:{json_path}") from exc
            if not raw:
                continue
            source, offset = source_offset(item, raw, ara, hagecho)
            key = (source, offset)
            decoded = item["text"]
            current = {
                "source": source,
                "file_offset": offset,
                "original_hex": raw.hex().upper(),
                "original_text": decoded,
                "cp932_text": raw.decode("cp932", errors="replace"),
                "byte_length": len(raw),
                "character_count": len(decoded),
                "block_index": block_for_offset(blocks, offset) if source == "HAGECHO.BIN" else None,
            }
            prior = entries.get(key)
            if prior is not None and prior != current:
                raise ValueError(f"conflicting source descriptions for {source}+0x{offset:X}")
            entries[key] = current
            reference = {"report": report_name, "json_path": json_path}
            if reference not in references[key]:
                references[key].append(reference)

    ordered = [entries[key] for key in sorted(entries)]
    if len(ordered) != 324:
        raise ValueError(f"confirmed SSOT denominator changed: expected 324, got {len(ordered)}")
    counts = defaultdict(int)
    for entry in ordered:
        counts[entry["source"]] += 1
        prefix = "ARA" if entry["source"] == "ARA.BIN" else "HAG"
        entry["id"] = f"TXT-{prefix}-{entry['file_offset']:06X}"
    if len({entry["id"] for entry in ordered}) != len(ordered):
        raise ValueError("duplicate stable text ID")
    return ordered, {f"{key[0]}+0x{key[1]:X}": value for key, value in references.items()}


def expected_rows(entries: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "id": entry["id"],
            "source": entry["source"],
            "file_offset_hex": f"0x{entry['file_offset']:X}",
            "block_index": "" if entry["block_index"] is None else str(entry["block_index"]),
            "original_hex": entry["original_hex"],
            "original_text": entry["original_text"],
            "translation": "",
            "status": "untranslated",
            "notes": "",
        }
        for entry in entries
    ]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != FIELDNAMES:
            raise ValueError(f"CSV columns changed: {reader.fieldnames}")
        return list(reader)


def validate_csv(rows: list[dict[str, str]], entries: list[dict[str, Any]]) -> None:
    if len(rows) != len(entries):
        raise ValueError(f"CSV row count changed: expected {len(entries)}, got {len(rows)}")
    expected = expected_rows(entries)
    immutable = FIELDNAMES[:6]
    for index, (row, baseline) in enumerate(zip(rows, expected, strict=True), start=2):
        for field in immutable:
            if row[field] != baseline[field]:
                raise ValueError(f"immutable CSV field changed at row {index}: {field}")
        if row["status"] not in {"untranslated", "translate", "draft", "reviewed", "approved", "excluded"}:
            raise ValueError(f"invalid translation status at row {index}: {row['status']!r}")
        if row["status"] in {"translate", "draft", "reviewed", "approved"} and not row["translation"].strip():
            raise ValueError(f"translated status without translation at row {index}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    project = args.project.resolve()
    csv_path = project / "workspace/text/rakugaki_text_ja_ko.csv"
    manifest_path = project / "workspace/text/rakugaki_text_manifest.json"
    report_path = project / "work/analysis/text_ssot_001.json"
    entries, references = collect(project)
    rows = expected_rows(entries)

    if args.check:
        if not csv_path.exists() or not manifest_path.exists():
            raise FileNotFoundError("SSOT files do not exist; run without --check once")
        rows = read_csv(csv_path)
        validate_csv(rows, entries)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["entries"] != entries or manifest["references"] != references:
            raise ValueError("text manifest no longer matches extracted source evidence")
    else:
        if csv_path.exists() or manifest_path.exists():
            raise FileExistsError("SSOT already exists; use --check so translations cannot be overwritten")
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "CONSUMER_CONFIRMED_DYNAMIC_TEXT_SSOT",
                    "immutable_csv_fields": FIELDNAMES[:6],
                    "editable_csv_fields": FIELDNAMES[6:],
                    "scope_limit": "confirmed decoder/atlas-builder dynamic-text paths; baked graphic text excluded",
                    "entries": entries,
                    "references": references,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        rows = read_csv(csv_path)
        validate_csv(rows, entries)

    status_counts = {
        status: sum(row["status"] == status for row in rows)
        for status in ("untranslated", "translate", "draft", "reviewed", "approved", "excluded")
    }
    covered = len(rows) - status_counts["untranslated"]
    if covered == len(rows):
        ssot_status = "SSOT_TRANSLATION_COMPLETE_UNREVIEWED"
    elif covered:
        ssot_status = "SSOT_TRANSLATION_IN_PROGRESS"
    else:
        ssot_status = "SSOT_EXPORTED_TRANSLATION_NOT_STARTED"
    report = {
        "schema_version": 1,
        "observation_id": "TEXT-SSOT-001",
        "status": ssot_status,
        "entry_count": len(entries),
        "source_counts": {
            "ARA.BIN": sum(entry["source"] == "ARA.BIN" for entry in entries),
            "HAGECHO.BIN": sum(entry["source"] == "HAGECHO.BIN" for entry in entries),
        },
        "unique_ids": len({entry["id"] for entry in entries}),
        "duplicate_physical_sources": 0,
        "translation_status_counts": status_counts,
        "roundtrip": {
            "csv_utf8_bom": csv_path.read_bytes().startswith(b"\xef\xbb\xbf"),
            "immutable_source_fields_match": True,
            "all_original_bytes_match_extracted_sources": True,
            "manifest_matches_analysis_reports": True,
        },
        "artifacts": {
            "csv": {"path": str(csv_path.relative_to(project)).replace("\\", "/"), "sha256": sha256(csv_path.read_bytes())},
            "manifest": {"path": str(manifest_path.relative_to(project)).replace("\\", "/"), "sha256": sha256(manifest_path.read_bytes())},
        },
        "next_gate": "run the complete-corpus glyph census and translation audit; human review and runtime PoC remain separate gates",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"status={report['status']} entries={len(entries)} ARA={report['source_counts']['ARA.BIN']} HAGECHO={report['source_counts']['HAGECHO.BIN']}")


if __name__ == "__main__":
    main()
