#!/usr/bin/env python3
"""Judge candidate-page occupancy from FONT-TWO-BANK-VRAM-002 probe captures."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

from PIL import Image


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def psx_rgb(value: int) -> tuple[int, int, int]:
    return (
        (value & 0x1F) * 255 // 31,
        ((value >> 5) & 0x1F) * 255 // 31,
        ((value >> 10) & 0x1F) * 255 // 31,
    )


def render_rect(data: bytes, width: int, height: int, target: Path) -> None:
    values = struct.unpack(f"<{width * height}H", data)
    image = Image.new("RGB", (width, height))
    image.putdata([psx_rgb(value) for value in values])
    image.save(target)


def diff_rects(before: bytes, after: bytes, width: int) -> dict | None:
    if before == after:
        return None
    changed = 0
    min_x = min_y = 1 << 30
    max_x = max_y = -1
    for offset in range(0, len(before), 2):
        if before[offset : offset + 2] != after[offset : offset + 2]:
            changed += 1
            index = offset // 2
            x, y = index % width, index // width
            min_x, max_x = min(min_x, x), max(max_x, x)
            min_y, max_y = min(min_y, y), max(max_y, y)
    return {
        "changed_words": changed,
        "bounds_in_page": [min_x, min_y, max_x - min_x + 1, max_y - min_y + 1],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--session", default="001")
    parser.add_argument("--labels", type=Path, default=None,
                        help="optional JSON mapping probe index (string) -> scene label")
    args = parser.parse_args()
    project = args.project.resolve()
    capture_path = project / "work/analysis" / f"font_two_bank_vram_002_capture_{args.session}.json"
    capture = json.loads(capture_path.read_text(encoding="utf-8"))
    rect_x, rect_y, rect_w, rect_h = capture["rect_words"]
    labels = {}
    if args.labels is not None:
        labels = json.loads(args.labels.read_text(encoding="utf-8"))

    graphics_dir = project / "work/graphics" / f"vram_occupancy_{args.session}"
    graphics_dir.mkdir(parents=True, exist_ok=True)

    probe_reports = []
    previous_by_rect: dict[tuple, bytes] = {}
    anomalies = []
    for probe in capture["probes"]:
        probe_rect = probe.get("rect_words", capture["rect_words"])
        entry = {
            "index": probe["index"],
            "scheduled_seconds": probe["scheduled_seconds"],
            "rect_words": list(probe_rect),
            "scene_label": labels.get(str(probe["index"])),
            "screenshot": probe.get("screenshot"),
        }
        if "skipped" in probe or "aborted" in probe:
            for key in ("skipped", "aborted"):
                if key in probe:
                    entry[key] = probe[key]
            if "aborted" in probe:
                anomalies.append(f"probe {probe['index']} aborted: {probe['aborted']}")
            probe_reports.append(entry)
            continue
        if not probe.get("stub_completed") or probe.get("stub_flag") != 0:
            anomalies.append(
                f"probe {probe['index']} stub anomaly: flag={probe.get('stub_flag')} completed={probe.get('stub_completed')}"
            )
        rect_path = project / probe["rect_file"]
        data = rect_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != probe["rect_sha256"]:
            raise ValueError(f"rect file hash mismatch for probe {probe['index']}")
        probe_w, probe_h = probe_rect[2], probe_rect[3]
        png_path = graphics_dir / f"probe_{probe['index']:03d}_rect.png"
        render_rect(data, probe_w, probe_h, png_path)
        rect_key = tuple(probe_rect)
        previous_data = previous_by_rect.get(rect_key)
        entry.update({
            "rect_sha256": probe["rect_sha256"],
            "rect_png": png_path.relative_to(project).as_posix(),
            "stub_flag": probe.get("stub_flag"),
            "diff_from_previous": None if previous_data is None else diff_rects(previous_data, data, probe_w),
        })
        probe_reports.append(entry)
        previous_by_rect[rect_key] = data

    measured = [entry for entry in probe_reports if "rect_sha256" in entry]
    skipped = [entry for entry in probe_reports if "skipped" in entry or "aborted" in entry]
    unique_hashes = sorted({entry["rect_sha256"] for entry in measured})
    changes = [entry for entry in measured if entry.get("diff_from_previous")]
    rect_groups = {}
    for entry in measured:
        group = rect_groups.setdefault(tuple(entry["rect_words"]), {"measured": 0, "hashes": set(), "changes": 0})
        group["measured"] += 1
        group["hashes"].add(entry["rect_sha256"])
        if entry.get("diff_from_previous"):
            group["changes"] += 1
    per_rect = [
        {
            "rect_words": list(key),
            "measured": group["measured"],
            "unique_hashes": len(group["hashes"]),
            "changes": group["changes"],
            "written": len(group["hashes"]) > 1,
        }
        for key, group in sorted(rect_groups.items())
    ]
    if not measured:
        status = "NO_MEASUREMENTS"
    elif any(group["written"] for group in per_rect):
        status = "RECT_WRITTEN_DURING_CAPTURED_SCENES"
    elif anomalies:
        status = "MEASURED_WITH_ANOMALIES"
    elif skipped:
        status = "RECT_STABLE_ACROSS_CAPTURED_SCENES_WITH_SAMPLE_GAPS"
    else:
        status = "RECT_STABLE_ACROSS_CAPTURED_SCENES"

    report = {
        "schema_version": 1,
        "observation_id": "FONT-TWO-BANK-VRAM-002",
        "session": args.session,
        "status": status,
        "capture_report": capture_path.relative_to(project).as_posix(),
        "rect_words": capture["rect_words"],
        "probe_count": len(capture["probes"]),
        "measured_count": len(measured),
        "skipped_count": len(skipped),
        "per_rect": per_rect,
        "unique_rect_hashes": unique_hashes,
        "change_events": [
            {"index": entry["index"], "diff": entry["diff_from_previous"]} for entry in changes
        ],
        "anomalies": anomalies,
        "probes": probe_reports,
        "limits": [
            "Stability is only proven for the scenes actually captured in this session's screenshots.",
            "A probe samples occupancy at an instant; writes that are fully overwritten back between probes are invisible.",
        ],
    }
    output = project / "work/analysis" / f"font_two_bank_vram_002_{args.session}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": status,
        "measured": len(measured),
        "unique_hashes": len(unique_hashes),
        "changes": len(changes),
        "anomalies": anomalies,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
