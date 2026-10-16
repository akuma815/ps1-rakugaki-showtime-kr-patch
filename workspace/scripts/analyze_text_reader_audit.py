#!/usr/bin/env python3
"""Audit decoder/glyph-builder callers outside the generic string renderer."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

from analyze_text_callsites import decode_nul
from analyze_text_population import OVERLAY_BASE, payload_offset, scan_calls


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ARA_RUNTIME_BASE = 0x7FFD6000
DECODER = 0x800430DC
GLYPH_BUILDER = 0x80042F28
GENERIC_CHAR_RENDERER = 0x80043298
GENERIC_STRING_RENDERER = 0x80043348
ALTERNATE_GLYPH_RENDERER = 0x8003CE18
TITLE_STRING_BUILDER = 0x80144CFC


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    output = args.output or project / "work/analysis/text_reader_audit_001.json"

    ara = (project / "extracted/ARA.BIN").read_bytes()
    hagecho = (project / "extracted/HAGECHO.BIN").read_bytes()
    blocks = json.loads((project / "work/analysis/hagecho_blocks.json").read_text(encoding="utf-8"))["blocks"]
    encoding = json.loads((project / "workspace/config/font_encoding.json").read_text(encoding="utf-8"))
    mapping = {int(item["sjis"], 16): item for item in encoding["mappings"]}

    expected_calls = {
        "decoder_ara": (ara, DECODER, [0x66E5C, 0x6D2D4]),
        "decoder_hagecho": (hagecho, DECODER, [0x19074]),
        "glyph_builder_ara": (ara, GLYPH_BUILDER, [0x66E9C, 0x6D308, 0x6D320]),
        "glyph_builder_hagecho": (hagecho, GLYPH_BUILDER, [0x1909C]),
        "generic_char_renderer_ara": (ara, GENERIC_CHAR_RENDERER, [0x6D394]),
        "generic_char_renderer_hagecho": (hagecho, GENERIC_CHAR_RENDERER, []),
        "alternate_renderer_ara": (ara, ALTERNATE_GLYPH_RENDERER, [0x96D68, 0x96DD8]),
        "alternate_renderer_hagecho": (hagecho, ALTERNATE_GLYPH_RENDERER, [0x22765C, 0x228028, 0x228090]),
        "title_builder_hagecho": (hagecho, TITLE_STRING_BUILDER, [0x1923C, 0x19250, 0x19268, 0x19288, 0x1929C, 0x192B4]),
    }
    call_inventory = {}
    for name, (data, target, expected) in expected_calls.items():
        actual = scan_calls(data, target)
        if actual != expected:
            raise ValueError(f"reader call inventory changed: {name}: {actual}")
        call_inventory[name] = {"target_runtime": f"0x{target:08X}", "count": len(actual), "file_offsets": actual}

    # No raw function-pointer words are stored for the decoder, glyph builder,
    # or generic character renderer. Their observed uses are direct JAL edges.
    raw_function_pointer_hits = {}
    for target in (DECODER, GLYPH_BUILDER, GENERIC_CHAR_RENDERER):
        needle = struct.pack("<I", target)
        raw_function_pointer_hits[f"0x{target:08X}"] = {
            "ARA.BIN": ara.count(needle),
            "HAGECHO.BIN": hagecho.count(needle),
        }
    if any(count for files in raw_function_pointer_hits.values() for count in files.values()):
        raise ValueError("unexpected stored function-pointer word found in reader audit")

    def ara_offset(runtime: int) -> int:
        offset = runtime - ARA_RUNTIME_BASE
        if not 0 <= offset < len(ara):
            raise ValueError(f"ARA address outside file: 0x{runtime:08X}")
        return offset

    def ara_source(runtime: int) -> dict:
        offset = ara_offset(runtime)
        return {"runtime_address": f"0x{runtime:08X}", "ara_offset": offset, **decode_nul(ara, offset, mapping, 0x400)}

    block0 = blocks[0]
    block6 = blocks[6]
    block0_payload = payload_offset(hagecho[block0["start"] : block0["end"]])
    block6_payload = payload_offset(hagecho[block6["start"] : block6["end"]])

    def hagecho_offset(block: dict, payload: int, runtime: int) -> int:
        offset = block["start"] + payload + runtime - OVERLAY_BASE
        if not block["start"] <= offset < block["end"]:
            raise ValueError(f"runtime address outside block {block['index']}: 0x{runtime:08X}")
        return offset

    def hagecho_source(block: dict, payload: int, runtime: int, limit: int = 0x800) -> dict:
        offset = hagecho_offset(block, payload, runtime)
        return {
            "runtime_address": f"0x{runtime:08X}",
            "file_offset": offset,
            "block_index": block["index"],
            **decode_nul(hagecho, offset, mapping, limit),
        }

    # ARA table used by the alternate one-glyph renderer. The next word is a
    # non-pointer scalar and fixes the table at 24 entries.
    death_table_runtime = 0x800A69A8
    death_messages = []
    for index in range(24):
        pointer_offset = ara_offset(death_table_runtime) + index * 4
        target = struct.unpack_from("<I", ara, pointer_offset)[0]
        death_messages.append({
            "index": index,
            "pointer_ara_offset": pointer_offset,
            "pointer_raw": f"0x{target:08X}",
            **ara_source(target),
        })
    next_death_word = struct.unpack_from("<I", ara, ara_offset(death_table_runtime) + 24 * 4)[0]
    if next_death_word != 0x6E2:
        raise ValueError("ARA death-message table boundary changed")

    # The other ARA producer is the 22-entry name/team table already cataloged
    # by TEXT-WRAPPER-PRODUCERS-001. It supplies one two-byte character at a time.
    name_table_runtime = 0x800A7064
    name_targets = [struct.unpack_from("<I", ara, ara_offset(name_table_runtime) + index * 4)[0] for index in range(22)]
    if len(set(name_targets)) != 22:
        raise ValueError("ARA name/team table changed")

    copyright_source = hagecho_source(block6, block6_payload, 0x801394E0)
    if copyright_source["text"] != "トレジャー／エニックス　１９９９":
        raise ValueError("block 6 copyright source changed")

    # Credits consist of two six-page outer tables. Five page lists are shared;
    # their final page differs. Each page has 8-byte records (control, pointer)
    # and ends with (0xFF, 0).
    credits_outer_tables = []
    unique_credit_sources: dict[int, dict] = {}
    for table_id, table_runtime in (("credits_variant_a", 0x801394A8), ("credits_variant_b", 0x801394C4)):
        table_offset = hagecho_offset(block6, block6_payload, table_runtime)
        pages = []
        for page_index in range(6):
            page_runtime = struct.unpack_from("<I", hagecho, table_offset + page_index * 4)[0]
            records = []
            record_index = 0
            while True:
                record_offset = hagecho_offset(block6, block6_payload, page_runtime) + record_index * 8
                control, target = struct.unpack_from("<II", hagecho, record_offset)
                if control == 0xFF and target == 0:
                    terminator = {"record_index": record_index, "control": "0x000000FF", "pointer_raw": "0x00000000"}
                    break
                if record_index >= 32:
                    raise ValueError("credits page lacks terminator")
                decoded = unique_credit_sources.setdefault(target, hagecho_source(block6, block6_payload, target))
                records.append({
                    "record_index": record_index,
                    "record_file_offset": record_offset,
                    "control": f"0x{control:08X}",
                    "pointer_raw": f"0x{target:08X}",
                    "source": decoded,
                })
                record_index += 1
            pages.append({
                "page_index": page_index,
                "runtime_address": f"0x{page_runtime:08X}",
                "record_count": len(records),
                "records": records,
                "terminator": terminator,
            })
        outer_null = struct.unpack_from("<I", hagecho, table_offset + 6 * 4)[0]
        if outer_null != 0:
            raise ValueError("credits outer table lacks NULL terminator")
        credits_outer_tables.append({
            "table_id": table_id,
            "runtime_address": f"0x{table_runtime:08X}",
            "page_count": len(pages),
            "pages": pages,
            "null_sentinel_index": 6,
        })
    if len(unique_credit_sources) != 50:
        raise AssertionError(f"credit source count changed: {len(unique_credit_sources)}")

    title_source = hagecho_source(block0, block0_payload, 0x80145D44)
    if title_source["text"] != "プッシュスタートボタン":
        raise ValueError("title fixed source changed")

    report = {
        "schema_version": 1,
        "observation_id": "TEXT-READER-AUDIT-001",
        "status": "ATLAS_TEXT_READER_AUDIT_COMPLETE",
        "scope": {
            "revision_bin_sha256": "99dc6c7f748c4882d6f197a0a0a2eb943affc25bb4271deff9bb9bf55859aa54",
            "ara_sha256": sha256(ara),
            "hagecho_sha256": sha256(hagecho),
            "scope_limit": "Static direct callers of the confirmed code-to-cell decoder and atlas primitive builder, plus their source producers.",
        },
        "consumer_inventory": {
            "decoder_direct_callers": 3,
            "glyph_builder_direct_callers": 4,
            "generic_character_renderer_callers": 1,
            "alternate_glyph_renderer_callers": 5,
            "title_builder_callers": 6,
            "call_inventory": call_inventory,
            "stored_function_pointer_word_hits": raw_function_pointer_hits,
            "classification": [
                {"consumer": "generic character renderer 0x80043298", "source": "generic string renderer 0x80043348", "status": "previously resolved"},
                {"consumer": "alternate glyph renderer 0x8003CE18", "source": "ARA name/team and death-message tables; HAGECHO credits and copyright sources", "status": "resolved"},
                {"consumer": "title builder 0x80144CFC", "source": "fixed HAGECHO 0x80145D44", "status": "previously resolved"},
            ],
            "unknown": 0,
        },
        "alternate_sources": {
            "ara_name_team_table_reused": {
                "runtime_address": f"0x{name_table_runtime:08X}",
                "entry_count": len(name_targets),
                "new_unique_coordinates": 0,
                "reason": "Already counted by TEXT-WRAPPER-PRODUCERS-001.",
            },
            "ara_death_message_table": {
                "runtime_address": f"0x{death_table_runtime:08X}",
                "entry_count": len(death_messages),
                "entries": death_messages,
                "next_word": f"0x{next_death_word:08X}",
            },
            "hagecho_credits": {
                "outer_tables": credits_outer_tables,
                "unique_string_coordinates": len(unique_credit_sources),
                "record_format": "8 bytes: control word + string pointer; (0xFF, 0) page terminator",
            },
            "hagecho_copyright": copyright_source,
            "title_source_reused": {"source": title_source, "new_unique_coordinates": 0},
        },
        "population_delta": {
            "new_unique_static_source_entries": 75,
            "breakdown": {"ARA_death_messages": 24, "HAGECHO_credits": 50, "HAGECHO_copyright": 1},
            "previous_confirmed_unique_static_source_entries": 249,
            "confirmed_unique_static_source_entries": 324,
        },
        "completion": {
            "forwarded_consumers_unknown": 0,
            "confirmed_atlas_decoder_reader_paths_unknown": 0,
            "dynamic_atlas_text_population_status": "CLOSED_FOR_CONFIRMED_DECODER_AND_BUILDER_PATHS",
            "not_proven": "Baked graphic text and any path that constructs primitives without the confirmed decoder/builder remain separate scope.",
            "hangul_allocation_status": "NO_SAFE_HANGUL_CODE_ALLOCATION_YET",
        },
        "roundtrip": {
            "all_expected_call_inventories_match": "PASS",
            "all_credits_controls_pointers_and_terminators_preserved": "PASS",
            "all_new_static_sources_decode_with_nul_boundaries": "PASS",
            "original_files_modified": False,
        },
        "next": [
            "Choose between atlas expansion and a decoder/font hook; direct code reuse remains blocked because all reachable cells are occupied.",
            "Keep baked graphic text as a separately cataloged translation scope.",
            "Build a representative menu-string reinsertion PoC only after selecting the Hangul code strategy.",
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {output}")
    print("confirmed decoder reader paths: unknown 0")
    print("new unique static coordinates: 75; confirmed total: 324")
    print("Hangul allocation remains blocked: no safe reachable cells")


if __name__ == "__main__":
    main()
