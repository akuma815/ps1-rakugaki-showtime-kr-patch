#!/usr/bin/env python3
"""Repair spreadsheet-formatted immutable SSOT fields without touching translations."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

from export_text_ssot import FIELDNAMES, expected_rows


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def editable_snapshot(rows: list[dict[str, str]]) -> list[tuple[str, str, str, str]]:
    return [(row["id"], row["translation"], row["status"], row["notes"]) for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    project = args.project.resolve()
    csv_path = project / "workspace/text/rakugaki_text_ja_ko.csv"
    manifest_path = project / "workspace/text/rakugaki_text_manifest.json"
    backup_dir = project / "backups"
    report_dir = project / "work/analysis"
    repair_index = 1
    while (report_dir / f"translation_ssot_repair_{repair_index:03d}.json").exists():
        repair_index += 1
    backup_path = backup_dir / f"rakugaki_text_ja_ko.pre_immutable_repair_{repair_index:03d}.csv"
    report_path = report_dir / f"translation_ssot_repair_{repair_index:03d}.json"

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != FIELDNAMES:
            raise ValueError(f"CSV columns changed: {reader.fieldnames}")
        rows = list(reader)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = expected_rows(manifest["entries"])
    if len(rows) != len(expected):
        raise ValueError("row count differs from manifest")
    if [row["id"] for row in rows] != [row["id"] for row in expected]:
        raise ValueError("stable IDs or row order changed; automatic repair refused")

    before_editable = editable_snapshot(rows)
    changes: list[dict[str, str | int]] = []
    for row_number, (row, baseline) in enumerate(zip(rows, expected, strict=True), start=2):
        for field in FIELDNAMES[:6]:
            if row[field] != baseline[field]:
                changes.append(
                    {
                        "row": row_number,
                        "id": row["id"],
                        "field": field,
                        "before": row[field],
                        "after": baseline[field],
                    }
                )
                row[field] = baseline[field]
    if not changes:
        raise ValueError("no immutable-field damage found; repair not required")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(csv_path, backup_path)
    temporary = csv_path.with_suffix(".csv.repairing")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(csv_path)

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        repaired = list(csv.DictReader(handle))
    if editable_snapshot(repaired) != before_editable:
        raise ValueError("editable translation fields changed during repair")
    report = {
        "schema_version": 1,
        "observation_id": f"TEXT-SSOT-REPAIR-{repair_index:03d}",
        "status": "IMMUTABLE_FIELDS_REPAIRED_TRANSLATIONS_PRESERVED",
        "row_count": len(rows),
        "change_count": len(changes),
        "changes_by_field": dict(sorted(Counter(change["field"] for change in changes).items())),
        "editable_fields_preserved": True,
        "translation_rows_preserved": sum(bool(row["translation"].strip()) for row in repaired),
        "backup": {"path": str(backup_path.relative_to(project)).replace("\\", "/"), "sha256": sha256(backup_path)},
        "repaired_csv": {"path": str(csv_path.relative_to(project)).replace("\\", "/"), "sha256": sha256(csv_path)},
        "changes": changes,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"status={report['status']} changes={len(changes)} translations={report['translation_rows_preserved']}")


if __name__ == "__main__":
    main()
