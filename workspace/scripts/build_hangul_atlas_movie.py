#!/usr/bin/env python3
"""Build the Korean game disc with the user-approved BAD movie subtitle.

The existing HANGUL-ATLAS-001 builder remains the base build. This script
rebuilds and validates that base, then applies only the changed Mode 2 Form 1
video sectors from the approved BAD.STR candidate. Original XA Form 2 sectors
and every sector outside the BAD extent remain byte-identical to the base.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from merge_psx_movie_video import collect_video_frames, split_sectors
from psx_disc import RAW_SECTOR_SIZE, expected_msf
from psx_edc_ecc import verify_mode2_form1


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_path(path: Path, project: Path) -> str:
    try:
        return path.relative_to(project).as_posix()
    except ValueError:
        return str(path)


def changed_sector_indices(before: bytes, after: bytes) -> list[int]:
    if len(before) != len(after):
        raise ValueError("sector comparison inputs have different sizes")
    if len(before) % RAW_SECTOR_SIZE:
        raise ValueError("sector comparison input is not raw-sector aligned")
    return [
        index
        for index in range(len(before) // RAW_SECTOR_SIZE)
        if before[index * RAW_SECTOR_SIZE:(index + 1) * RAW_SECTOR_SIZE]
        != after[index * RAW_SECTOR_SIZE:(index + 1) * RAW_SECTOR_SIZE]
    ]


def read_raw_extent(image: Path, lba: int, sector_count: int) -> bytes:
    with image.open("rb") as source:
        source.seek(lba * RAW_SECTOR_SIZE)
        data = source.read(sector_count * RAW_SECTOR_SIZE)
    if len(data) != sector_count * RAW_SECTOR_SIZE:
        raise ValueError(f"raw extent leaves image: LBA {lba}, sectors {sector_count}")
    return data


def diff_image_sectors(before_path: Path, after_path: Path, batch_sectors: int = 256) -> set[int]:
    if before_path.stat().st_size != after_path.stat().st_size:
        raise ValueError("disc images have different sizes")
    size = before_path.stat().st_size
    if size % RAW_SECTOR_SIZE:
        raise ValueError("disc image size is not raw-sector aligned")
    sector_count = size // RAW_SECTOR_SIZE
    changed: set[int] = set()
    with before_path.open("rb") as before, after_path.open("rb") as after:
        for start in range(0, sector_count, batch_sectors):
            count = min(batch_sectors, sector_count - start)
            byte_count = count * RAW_SECTOR_SIZE
            before_block = before.read(byte_count)
            after_block = after.read(byte_count)
            if len(before_block) != byte_count or len(after_block) != byte_count:
                raise ValueError("short read while comparing disc images")
            if before_block == after_block:
                continue
            for relative in range(count):
                offset = relative * RAW_SECTOR_SIZE
                if before_block[offset:offset + RAW_SECTOR_SIZE] != after_block[offset:offset + RAW_SECTOR_SIZE]:
                    changed.add(start + relative)
    return changed


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def require_hash(path: Path, expected: str, label: str) -> None:
    actual = sha256_file(path)
    if actual.lower() != expected.lower():
        raise ValueError(f"{label} hash mismatch: {actual} != {expected}")


def validate_approved_subtitle(csv_path: Path, subtitle: dict) -> dict:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    matches = [row for row in rows if row["subtitle_id"] == subtitle["subtitle_id"]]
    if len(matches) != 1:
        raise ValueError(f"expected one subtitle row {subtitle['subtitle_id']}, found {len(matches)}")
    row = matches[0]
    if row["movie"].upper() != subtitle["movie"].upper():
        raise ValueError("approved subtitle movie differs from build config")
    if row["status"].lower() != subtitle["expected_status"].lower():
        raise ValueError(f"subtitle {subtitle['subtitle_id']} is not approved")
    if not row["korean"].strip():
        raise ValueError("approved subtitle has no Korean text")
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("workspace/config/hangul_atlas_movie_001.json"),
    )
    parser.add_argument(
        "--rebuild-base",
        action="store_true",
        help="rebuild HANGUL-ATLAS-001 first; the default only validates and reuses that existing base",
    )
    args = parser.parse_args()

    project = args.project.resolve()
    config_path = args.config if args.config.is_absolute() else project / args.config
    config = load_json(config_path)
    manifest_path = project / "workspace/config/source_manifest.json"
    manifest = load_json(manifest_path)
    source_bin = project / manifest["disc"]["bin"]
    source_cue = project / manifest["disc"]["cue"]
    require_hash(source_bin, manifest["disc"]["bin_sha256"], "source BIN")
    require_hash(source_cue, manifest["disc"]["cue_sha256"], "source CUE")
    if source_bin.stat().st_size != manifest["disc"]["bin_size"]:
        raise ValueError("source BIN size differs from manifest")

    base_config = config["base_build"]
    base_script = project / base_config["script"]
    base_report_path = project / base_config["report"]
    if args.rebuild_base:
        subprocess.run([sys.executable, str(base_script), "--project", str(project)], cwd=project, check=True)
    if not base_report_path.is_file():
        raise FileNotFoundError(f"base build report is missing; run {base_script} first")
    base_report = load_json(base_report_path)
    if base_report["status"] != base_config["expected_status"]:
        raise ValueError(f"base build status differs: {base_report['status']}")
    base_bin = project / base_report["output"]["bin"]
    base_cue = project / base_report["output"]["cue"]
    require_hash(base_bin, base_report["output"]["bin_sha256"], "base BIN")
    require_hash(base_cue, base_report["output"]["cue_sha256"], "base CUE")
    if base_bin.stat().st_size != source_bin.stat().st_size:
        raise ValueError("base BIN size differs from source")

    subtitle = config["subtitle"]
    subtitle_path = project / subtitle["csv"]
    require_hash(subtitle_path, subtitle["csv_sha256"], "subtitle CSV")
    subtitle_row = validate_approved_subtitle(subtitle_path, subtitle)

    movie_config = config["movie"]
    catalog_path = project / movie_config["catalog"]
    require_hash(catalog_path, movie_config["catalog_sha256"], "movie catalog")
    catalog = load_json(catalog_path)
    movie_matches = [item for item in catalog["movies"] if item["asset_id"] == movie_config["asset_id"]]
    if len(movie_matches) != 1:
        raise ValueError(f"movie catalog asset count differs for {movie_config['asset_id']}")
    movie = movie_matches[0]
    lba = int(movie["iso_lba"])
    sector_count = int(movie["sector_count"])
    if sector_count != movie_config["expected_sector_count"]:
        raise ValueError("movie sector count differs from approved config")
    if int(movie["last_lba"]) != lba + sector_count - 1:
        raise ValueError("movie catalog LBA range is inconsistent")

    candidate_path = project / movie_config["candidate"]
    require_hash(candidate_path, movie_config["candidate_sha256"], "approved STR candidate")
    candidate_data = candidate_path.read_bytes()
    if len(candidate_data) != sector_count * RAW_SECTOR_SIZE:
        raise ValueError("approved STR candidate size differs from catalog raw extent")

    report_specs = [
        ("donor_report", "donor_report_sha256"),
        ("merge_report", "merge_report_sha256"),
        ("decode_report", "decode_report_sha256"),
        ("alignment_report", "alignment_report_sha256"),
    ]
    reports: dict[str, dict] = {}
    report_paths: dict[str, Path] = {}
    for path_key, hash_key in report_specs:
        report_path = project / movie_config[path_key]
        require_hash(report_path, movie_config[hash_key], path_key)
        reports[path_key] = load_json(report_path)
        report_paths[path_key] = report_path
    donor_report = reports["donor_report"]
    merge_report = reports["merge_report"]
    decode_report = reports["decode_report"]
    alignment_report = reports["alignment_report"]
    if donor_report["included_statuses"] != ["approved"]:
        raise ValueError("donor was not generated from approved-only subtitles")
    if donor_report["input_subtitles_sha256"].lower() != subtitle["csv_sha256"].lower():
        raise ValueError("donor subtitle CSV hash differs")
    if merge_report["output_sha256"].lower() != movie_config["candidate_sha256"].lower():
        raise ValueError("merge report does not identify the approved STR")
    if merge_report["source_donor_report_sha256"].lower() != movie_config["donor_report_sha256"].lower():
        raise ValueError("merge report donor-report link differs")
    if not merge_report["xa_byte_identical"] or not merge_report["validation"]["all_changed_video_sectors_edc_ecc_valid"]:
        raise ValueError("approved STR merge validation did not pass")
    if decode_report["status"] != "CANDIDATE_REVIEW_DECODE_PASS_RUNTIME_NOT_BUILT":
        raise ValueError("approved STR decode report did not pass")
    if decode_report["input_sha256"].lower() != movie_config["candidate_sha256"].lower():
        raise ValueError("decode report candidate hash differs")
    if alignment_report["status"] != "FRAME_ALIGNMENT_STATIC_PASS" or alignment_report["best_shift"] != 0:
        raise ValueError("approved STR frame alignment did not pass at zero shift")
    if alignment_report["candidate_sha256"].lower() != movie_config["candidate_sha256"].lower():
        raise ValueError("alignment report candidate hash differs")

    original_str_path = project / movie["output"]
    require_hash(original_str_path, movie["raw_sha256"], "extracted original STR")
    original_data = original_str_path.read_bytes()
    if read_raw_extent(source_bin, lba, sector_count) != original_data:
        raise ValueError("source BIN BAD extent differs from extracted original STR")
    if read_raw_extent(base_bin, lba, sector_count) != original_data:
        raise ValueError("base build changed the BAD extent before movie integration")

    original_sectors = split_sectors(original_data)
    candidate_sectors = split_sectors(candidate_data)
    changed_relative = changed_sector_indices(original_data, candidate_data)
    original_frames = collect_video_frames(original_sectors)
    candidate_frames = collect_video_frames(candidate_sectors)
    if sorted(original_frames) != sorted(candidate_frames):
        raise ValueError("candidate STR frame numbers differ from original")
    video_indices = {
        item["sector_index"]
        for chunks in original_frames.values()
        for item in chunks.values()
    }
    if len(video_indices) != movie_config["expected_video_sector_count"]:
        raise ValueError("video sector count differs from approved config")
    if len(changed_relative) != movie_config["expected_changed_video_sector_count"]:
        raise ValueError("changed video sector count differs from approved config")
    if not set(changed_relative) <= video_indices:
        raise ValueError("approved STR changes a non-video sector")
    nonvideo_indices = set(range(sector_count)) - video_indices
    if len(nonvideo_indices) != movie_config["expected_xa_sector_count"]:
        raise ValueError("XA/non-video sector count differs from approved config")
    if any(candidate_sectors[index] != original_sectors[index] for index in nonvideo_indices):
        raise ValueError("approved STR changes an original XA sector")
    for relative, (original_sector, candidate_sector) in enumerate(zip(original_sectors, candidate_sectors, strict=True)):
        target_lba = lba + relative
        if candidate_sector[:24] != original_sector[:24]:
            raise ValueError(f"candidate changed raw header/subheader at LBA {target_lba}")
        if candidate_sector[12:15] != expected_msf(target_lba) or candidate_sector[15] != 2:
            raise ValueError(f"candidate raw MSF/mode differs at LBA {target_lba}")
        if candidate_sector[16:20] != candidate_sector[20:24]:
            raise ValueError(f"candidate duplicate subheader differs at LBA {target_lba}")
    if not all(verify_mode2_form1(candidate_sectors[index]) for index in video_indices):
        raise ValueError("approved STR contains an invalid Form 1 video sector")

    base_expected = base_report["expected_writes"]["sectors"]
    base_expected_lbas = [int(item["lba"]) for item in base_expected]
    if len(base_expected_lbas) != len(set(base_expected_lbas)):
        raise ValueError("base Expected Write list contains duplicate LBAs")
    movie_lbas = {lba + index for index in changed_relative}
    if movie_lbas & set(base_expected_lbas):
        raise ValueError("movie Expected Writes overlap the base build")
    actual_base_lbas = diff_image_sectors(source_bin, base_bin)
    if actual_base_lbas != set(base_expected_lbas):
        raise ValueError("base BIN changes differ from its Expected Write report")

    output_config = config["output"]
    output_dir = project / "build" / output_config["build_id"]
    output_dir.mkdir(parents=True, exist_ok=True)
    output_bin = output_dir / output_config["bin_name"]
    output_cue = output_bin.with_suffix(".cue")
    temporary = output_bin.with_suffix(output_bin.suffix + ".tmp")
    temporary_cue = output_cue.with_suffix(output_cue.suffix + ".tmp")
    for path in (temporary, temporary_cue):
        if path.exists():
            path.unlink()

    movie_writes: list[dict] = []
    actual_movie_lbas: set[int]
    actual_final_lbas: set[int]
    neighbors: list[dict]
    untouched_movies: list[dict]
    try:
        # Assemble the release candidate from the immutable original, not by
        # treating an older patched image as a new source. The freshly rebuilt
        # base BIN is only the provider of the 91 already-validated final raw
        # sectors recorded by its Expected Write report.
        shutil.copyfile(source_bin, temporary)
        with base_bin.open("rb") as base_source, temporary.open("r+b") as output:
            for record in base_expected:
                target_lba = int(record["lba"])
                output.seek(target_lba * RAW_SECTOR_SIZE)
                expected = output.read(RAW_SECTOR_SIZE)
                if sha256_bytes(expected) != record["expected_sha256"]:
                    raise ValueError(f"base Expected Write source mismatch at LBA {target_lba}")
                base_source.seek(target_lba * RAW_SECTOR_SIZE)
                final = base_source.read(RAW_SECTOR_SIZE)
                if sha256_bytes(final) != record["final_sha256"]:
                    raise ValueError(f"base Expected Write final mismatch at LBA {target_lba}")
                output.seek(target_lba * RAW_SECTOR_SIZE)
                output.write(final)
            for relative in changed_relative:
                target_lba = lba + relative
                expected = original_sectors[relative]
                final = candidate_sectors[relative]
                output.seek(target_lba * RAW_SECTOR_SIZE)
                if output.read(RAW_SECTOR_SIZE) != expected:
                    raise ValueError(f"Expected Write mismatch at LBA {target_lba}")
                output.seek(target_lba * RAW_SECTOR_SIZE)
                output.write(final)
                movie_writes.append({
                    "owner": "MOVIE/BAD.STR approved Korean subtitle video",
                    "lba": target_lba,
                    "relative_sector": relative,
                    "expected_sha256": sha256_bytes(expected),
                    "final_sha256": sha256_bytes(final),
                    "raw_changed_byte_count": sum(a != b for a, b in zip(expected, final, strict=True)),
                })
            output.flush()
            os.fsync(output.fileno())
        # Validate the complete temporary image before it can replace a known-good
        # final build. This includes the global diff set, target extent, adjacent
        # sectors and every non-target movie extent.
        if temporary.stat().st_size != source_bin.stat().st_size:
            raise ValueError("final BIN size changed")
        if read_raw_extent(temporary, lba, sector_count) != candidate_data:
            raise ValueError("final BIN BAD extent differs from the approved candidate")
        actual_movie_lbas = diff_image_sectors(base_bin, temporary)
        if actual_movie_lbas != movie_lbas:
            raise ValueError("final BIN changes outside the approved movie Expected Writes")
        actual_final_lbas = diff_image_sectors(source_bin, temporary)
        expected_final_lbas = set(base_expected_lbas) | movie_lbas
        if actual_final_lbas != expected_final_lbas:
            raise ValueError("final BIN changes differ from the combined Expected Write plan")

        neighbor_lbas = [lba - 1, lba + sector_count]
        neighbors = []
        for neighbor_lba in neighbor_lbas:
            source_sector = read_raw_extent(source_bin, neighbor_lba, 1)
            output_sector = read_raw_extent(temporary, neighbor_lba, 1)
            if output_sector != source_sector:
                raise ValueError(f"movie neighbor sector changed at LBA {neighbor_lba}")
            neighbors.append({"lba": neighbor_lba, "sha256": sha256_bytes(source_sector)})

        untouched_movies = []
        for other in catalog["movies"]:
            if other["asset_id"] == movie["asset_id"]:
                continue
            other_start = int(other["iso_lba"])
            other_count = int(other["sector_count"])
            other_lbas = range(other_start, other_start + other_count)
            if any(other_lba in actual_final_lbas for other_lba in other_lbas):
                raise ValueError(f"non-target movie changed: {other['disc_path']}")
            untouched_movies.append({
                "asset_id": other["asset_id"],
                "disc_path": other["disc_path"],
                "lba_start": other_start,
                "lba_end": other_start + other_count - 1,
                "raw_sha256": other["raw_sha256"],
            })

        cue_text, count = re.subn(
            r'(?im)^FILE\s+"[^"]+"\s+BINARY$',
            f'FILE "{output_bin.name}" BINARY',
            source_cue.read_text(encoding="ascii"),
        )
        if count != 1:
            raise ValueError("expected exactly one CUE FILE line")
        with temporary_cue.open("w", encoding="ascii", newline="") as cue_output:
            cue_output.write(cue_text)
            cue_output.flush()
            os.fsync(cue_output.fileno())

        temporary.replace(output_bin)
        temporary_cue.replace(output_cue)
    finally:
        for path in (temporary, temporary_cue):
            if path.exists():
                path.unlink()

    report = {
        "schema_version": 1,
        "observation_id": config["observation_id"],
        "status": "STATIC_BUILD_PASS_DUCKSTATION_RUNTIME_PENDING",
        "config": relative_path(config_path, project),
        "config_sha256": sha256_file(config_path),
        "source": {
            "manifest": relative_path(manifest_path, project),
            "manifest_sha256": sha256_file(manifest_path),
            "bin": relative_path(source_bin, project),
            "bin_sha256": sha256_file(source_bin),
            "cue": relative_path(source_cue, project),
            "cue_sha256": sha256_file(source_cue),
        },
        "base_build": {
            "script": relative_path(base_script, project),
            "script_sha256": sha256_file(base_script),
            "report": relative_path(base_report_path, project),
            "report_sha256": sha256_file(base_report_path),
            "bin": relative_path(base_bin, project),
            "bin_sha256": sha256_file(base_bin),
            "expected_write_sector_count": len(base_expected_lbas),
        },
        "subtitle": {
            "csv": relative_path(subtitle_path, project),
            "csv_sha256": sha256_file(subtitle_path),
            "row": subtitle_row,
        },
        "movie": {
            "asset_id": movie["asset_id"],
            "disc_path": movie["disc_path"],
            "lba_start": lba,
            "lba_end": lba + sector_count - 1,
            "raw_byte_offset": lba * RAW_SECTOR_SIZE,
            "raw_byte_length": sector_count * RAW_SECTOR_SIZE,
            "sector_count": sector_count,
            "source_str": relative_path(original_str_path, project),
            "source_str_sha256": sha256_file(original_str_path),
            "candidate": relative_path(candidate_path, project),
            "candidate_sha256": sha256_file(candidate_path),
            "video_sector_count": len(video_indices),
            "xa_sector_count": len(nonvideo_indices),
            "changed_video_sector_count": len(changed_relative),
            "reports": {
                path_key: {
                    "path": relative_path(report_paths[path_key], project),
                    "sha256": sha256_file(report_paths[path_key]),
                    "status": reports[path_key]["status"],
                }
                for path_key, _ in report_specs
            },
            "neighbor_sectors": neighbors,
            "untouched_other_movies": untouched_movies,
        },
        "expected_writes": {
            "base_sector_count": len(base_expected),
            "movie_sector_count": len(movie_writes),
            "combined_sector_count": len(base_expected) + len(movie_writes),
            "base_sectors": base_expected,
            "movie_sectors": movie_writes,
        },
        "validation": {
            "source_and_base_hashes_verified": True,
            "temporary_image_started_from_immutable_original": True,
            "approved_subtitle_hash_and_status_verified": True,
            "candidate_report_chain_verified": True,
            "source_bin_extent_equals_extracted_original": True,
            "base_bin_extent_equals_extracted_original": True,
            "all_original_xa_sectors_byte_identical": True,
            "all_candidate_video_sectors_are_valid_form1": True,
            "all_candidate_raw_headers_msf_and_subheaders_match_original": True,
            "movie_expected_writes_do_not_overlap_base": True,
            "base_diff_equals_base_expected_writes": True,
            "final_diff_equals_combined_expected_writes": True,
            "final_bad_extent_equals_approved_candidate": True,
            "bad_neighbor_sectors_byte_identical": True,
            "all_six_other_movie_extents_byte_identical": True,
            "cue_file_line_points_to_output_bin": True,
            "output_size_preserved": output_bin.stat().st_size == source_bin.stat().st_size,
        },
        "output": {
            "bin": relative_path(output_bin, project),
            "bin_sha256": sha256_file(output_bin),
            "cue": relative_path(output_cue, project),
            "cue_sha256": sha256_file(output_cue),
            "size": output_bin.stat().st_size,
        },
        "runtime_gate": (
            "DuckStation fresh boot: play BAD ending and verify subtitle visibility at f175-230, "
            "XA audio sync, continuous playback, skip behavior, movie completion and ending transition"
        ),
    }
    report_path = project / "work/analysis" / f"{output_config['build_id']}_build.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "base_expected_writes": len(base_expected),
        "movie_expected_writes": len(movie_writes),
        "combined_expected_writes": len(base_expected) + len(movie_writes),
        "output": report["output"]["bin"],
        "output_sha256": report["output"]["bin_sha256"],
        "report": relative_path(report_path, project),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
