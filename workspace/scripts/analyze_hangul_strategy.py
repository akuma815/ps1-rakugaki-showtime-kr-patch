#!/usr/bin/env python3
"""Validate and record the selected Hangul font strategy."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    project = args.project.resolve()
    output = args.output or project / "work/analysis/hangul_strategy_001.json"
    targets_path = project / "workspace/config/asset_targets.json"
    encoding_path = project / "workspace/config/font_encoding.json"
    reader_path = project / "work/analysis/text_reader_audit_001.json"
    census_path = project / "work/analysis/glyph_census_001.json"
    targets = json.loads(targets_path.read_text(encoding="utf-8"))
    encoding = json.loads(encoding_path.read_text(encoding="utf-8"))
    reader = json.loads(reader_path.read_text(encoding="utf-8"))
    census = json.loads(census_path.read_text(encoding="utf-8")) if census_path.exists() else None
    two_bank_poc = targets.get("two_bank_poc")
    two_bank_abi_pass = two_bank_poc is not None and two_bank_poc.get("status") == "BANK_AWARE_ABI_RUNTIME_PASS_ASSET_PERSISTENCE_OPEN"

    atlas = targets["targets"]["primary_font_atlas"]
    if atlas["dimensions"] != "256x256":
        raise ValueError(f"unexpected atlas dimensions: {atlas['dimensions']}")
    upload = atlas["runtime_upload"]
    if upload["image_dimensions_words"] != "64x256":
        raise ValueError(f"unexpected upload dimensions: {upload['image_dimensions_words']}")
    x, y = (int(value) for value in upload["image_destination_from_tim"].split(","))
    width_words, height = (int(value) for value in upload["image_dimensions_words"].split("x"))
    if (x + width_words, y + height) != (1024, 512):
        raise ValueError("font upload no longer ends at both PS1 VRAM boundaries")

    mappings = encoding["mappings"]
    reachable_cells = {
        int(item["cell"], 16) if isinstance(item["cell"], str) else int(item["cell"])
        for item in mappings
    }
    valid_inputs = len(mappings)
    if (valid_inputs, len(reachable_cells)) != (257, 254):
        raise ValueError("decoder mapping inventory changed")
    if targets["text_population"]["currently_proven_free_reachable_cells"] != 0:
        raise ValueError("safe-cell premise changed")

    reader_unknown = targets["text_population"]["reader_audit"]["confirmed_decoder_reader_paths_unknown"]
    if reader_unknown != 0:
        raise ValueError("decoder reader audit is no longer closed")

    overflow_confirmed = census is not None and census["status"] == "FIXED_ATLAS_OVERFLOW_TWO_BANK_POC_REQUIRED"
    result = {
        "schema_version": 1,
        "observation_id": "HANGUL-STRATEGY-001",
        "status": (
            "TWO_BANK_ABI_RUNTIME_PASS_ASSET_PERSISTENCE_OPEN"
            if two_bank_abi_pass
            else "STATIC_TWO_BANK_ATLAS_HOOK_POC_SELECTED"
            if overflow_confirmed
            else "DECODER_REMAP_FIXED_ATLAS_PRIMARY_CORPUS_CENSUS_REQUIRED"
        ),
        "decision": {
            "primary": "CORPUS_SCOPED_PRECOMPOSED_HANGUL_WITH_SHARED_DECODER_REMAP",
            "fallback": "STATIC_BANKED_ATLAS_WITH_DECODER_AND_PRIMITIVE_BUILDER_HOOK",
            "rejected": [
                "DIRECT_EXISTING_CODE_REUSE",
                "IN_PLACE_ATLAS_ENLARGEMENT",
                "COMPOSITIONAL_JAMO_AS_FIRST_IMPLEMENTATION",
                "EVICTING_DYNAMIC_GLYPH_CACHE_AS_FIRST_IMPLEMENTATION",
            ],
        },
        "complete_corpus_result": None if census is None else {
            "status": census["status"],
            "translated_rows": census["ssot"]["translated_rows"],
            "ssot_rows": census["ssot"]["rows"],
            "unique_required_glyphs": census["census"]["unique_required_glyphs"],
            "hangul_syllables": census["census"]["class_counts"].get("hangul_syllable", 0),
            "minimum_static_banks": census["census"].get("minimum_static_banks"),
            "minimum_static_bank_capacity": census["census"].get("minimum_static_bank_capacity"),
            "minimum_static_bank_headroom": census["census"].get("minimum_static_bank_headroom"),
            "selected": "STATIC_BANKED_ATLAS_WITH_DECODER_AND_PRIMITIVE_BUILDER_HOOK" if overflow_confirmed else "PENDING",
        },
        "confirmed_constraints": {
            "atlas_pixels": {"width": 256, "height": 256},
            "glyph_pixels": {"width": 16, "height": 16},
            "physical_cells": 256,
            "usable_non_fallback_cells_upper_bound": 255,
            "currently_occupied_cells_00_to_fe": 255,
            "currently_proven_free_reachable_cells": 0,
            "decoder_valid_inputs": valid_inputs,
            "decoder_unique_reachable_cells": len(reachable_cells),
            "cell_ff_role": "blank/fallback; consumed by CP932 8140",
            "uv_address_width_bits": 8,
            "uv_rule": targets["text_engine"]["uv_rule"],
            "vram_image_rect_words": {"x": x, "y": y, "width": width_words, "height": height},
            "vram_image_rect_end": {"x": x + width_words, "y": y + height},
            "ps1_vram_extent_words": {"width": 1024, "height": 512},
            "in_place_growth_possible": False,
            "confirmed_decoder_reader_paths_unknown": reader_unknown,
            "confirmed_static_source_entries": targets["text_population"]["confirmed_unique_static_source_entries"],
        },
        "primary_design": {
            "source_encoding": "NUL-terminated fixed two-byte tokens; one Korean syllable or symbol per token",
            "hook": {"runtime": targets["text_engine"]["decoder_runtime"], "return": "8-bit atlas cell"},
            "unchanged_components": [
                targets["text_engine"]["primitive_builder_runtime"],
                "font VRAM image rectangle and CLUT rows",
                "generic, alternate-glyph, and title motion primitive consumers",
            ],
            "allocation_rule": "Rebuild the 255 non-fallback cells from the complete translated corpus and a declared preserved-symbol set; do not infer free cells from the Japanese source.",
            "capacity_gate": "unique required glyphs including preserved symbols must be <= 255",
            "overflow_action": "Fail the build and advance to the banked-atlas PoC; never shorten translations merely to satisfy the font budget.",
        },
        "fallback_design": {
            "trigger": "complete-corpus glyph demand exceeds 255 or required simultaneous symbol preservation makes the fixed atlas infeasible",
            "minimum_poc": "two static 256x256 4bpp banks with explicit bank+cell mapping",
            "required_changes": [
                "allocate and upload a second texture page without assuming the Options capture proves global VRAM safety",
                "return or propagate bank information beyond the current 8-bit decoder result",
                "set the correct tpage/UV in every confirmed primitive-builder consumer path",
                "verify title UV reversal, motion, clipping, transitions, and alternate-glyph paths",
            ],
            "runtime_evidence_open": (
                ["globally safe second-bank VRAM rectangle, upload path, and lifetime across representative game states"]
                if two_bank_abi_pass
                else [
                    "globally safe second-bank VRAM rectangle across representative game states",
                    "bank-aware primitive ABI and lifetime",
                ]
            ),
        },
        "next_gate": {
            "goal": (
                "audit runtime occupancy and upload lifetime for the static-clear second-bank VRAM probe, then complete the visual title E2E PoC"
                if two_bank_abi_pass
                else "build the static two-bank decoder/primitive-builder hook PoC"
                if overflow_confirmed
                else "create the translatable-text SSOT and Korean draft/census, then choose fixed-atlas build or two-bank PoC from the measured glyph count"
            ),
            "runtime_claim": (
                "decoder bank bit to shared builder tpage/UV is runtime PASS; final bank-1 asset persistence and visual E2E remain open"
                if two_bank_abi_pass
                else "strategy only; no Korean decoder hook has been built or run yet"
            ),
        },
        "evidence": {
            str(targets_path.relative_to(project)).replace("\\", "/"): sha256(targets_path),
            str(encoding_path.relative_to(project)).replace("\\", "/"): sha256(encoding_path),
            str(reader_path.relative_to(project)).replace("\\", "/"): sha256(reader_path),
            **({str(census_path.relative_to(project)).replace("\\", "/"): sha256(census_path)} if census_path.exists() else {}),
        },
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {output}")
    print(f"status={result['status']}")


if __name__ == "__main__":
    main()
