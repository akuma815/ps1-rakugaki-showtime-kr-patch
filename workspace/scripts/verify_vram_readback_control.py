#!/usr/bin/env python3
"""Validate the GP0 C0h readback mechanism against the known bank-0 font atlas page."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from psx_tim import parse_tim


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--session", default="poscheck")
    args = parser.parse_args()
    project = args.project.resolve()

    capture_path = project / "work/analysis" / f"font_two_bank_vram_002_capture_{args.session}.json"
    capture = json.loads(capture_path.read_text(encoding="utf-8"))

    targets = json.loads((project / "workspace/config/asset_targets.json").read_text(encoding="utf-8"))
    offset = targets["targets"]["primary_font_atlas"]["container_offset"]
    hagecho = (project / "extracted/HAGECHO.BIN").read_bytes()
    tim = parse_tim(hagecho, offset)
    if tim is None:
        raise ValueError("font target no longer parses")
    image = tim["image"]
    expected_rect = [image["x"], image["y"], image["width_words"], image["height"]]
    if capture["rect_words"] != expected_rect:
        raise ValueError(f"control session rect {capture['rect_words']} != atlas rect {expected_rect}")
    payload = hagecho[image["payload_offset"] : image["payload_offset"] + image["payload_size"]]
    payload_sha = hashlib.sha256(payload).hexdigest()

    comparisons = []
    for probe in capture["probes"]:
        if "rect_file" not in probe:
            comparisons.append({"index": probe["index"], "skipped": probe.get("skipped")})
            continue
        readback = (project / probe["rect_file"]).read_bytes()
        comparisons.append({
            "index": probe["index"],
            "scheduled_seconds": probe["scheduled_seconds"],
            "readback_sha256": hashlib.sha256(readback).hexdigest(),
            "matches_tim_payload": readback == payload,
        })

    matched = [item for item in comparisons if item.get("matches_tim_payload")]
    status = "READBACK_MECHANISM_VALIDATED" if matched and all(
        item.get("matches_tim_payload") for item in comparisons if "matches_tim_payload" in item
    ) else "READBACK_MISMATCH"
    report = {
        "schema_version": 1,
        "observation_id": "FONT-TWO-BANK-VRAM-002",
        "role": "positive-control",
        "session": args.session,
        "status": status,
        "control_rect_words": expected_rect,
        "tim_container_offset": offset,
        "tim_payload_sha256": payload_sha,
        "comparisons": comparisons,
        "interpretation": (
            "An exact byte match between the injected GP0 C0h readback and the static font atlas TIM payload "
            "proves the probe pipeline reads real VRAM; it makes the all-zero candidate-page readbacks meaningful."
        ),
    }
    output = project / "work/analysis" / f"font_two_bank_vram_002_{args.session}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "matched_probes": len(matched)}, indent=2))


if __name__ == "__main__":
    main()
