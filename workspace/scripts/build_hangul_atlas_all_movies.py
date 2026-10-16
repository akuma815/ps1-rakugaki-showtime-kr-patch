#!/usr/bin/env python3
"""Build cheat/no-cheat Korean discs with all seven subtitled STR movies."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

from build_hangul_atlas_movie import (
    diff_image_sectors,
    load_json,
    read_raw_extent,
    relative_path,
    require_hash,
    sha256_bytes,
    sha256_file,
)
from merge_psx_movie_video import collect_video_frames, split_sectors
from psx_disc import RAW_SECTOR_SIZE, expected_msf
from psx_edc_ecc import verify_mode2_form1


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


MOVIE_INPUTS = {
    "BAD": ("workspace/text/rakugaki_movie_subtitles_ja_ko.csv", ["approved"]),
    "GOOD": ("work/video/movies/subtitle_preview/GOOD_dialogue_rescan_draft_002.csv", ["review_needed", "translated"]),
    "NE_A": ("work/video/movies/subtitle_preview/NE_A_dialogue_rescan_draft_001.csv", ["review_needed", "translated"]),
    "NE_B": ("work/video/movies/subtitle_preview/NE_B_dialogue_rescan_draft_001.csv", ["review_needed", "translated"]),
    "NE_C": ("work/video/movies/subtitle_preview/NE_C_dialogue_rescan_draft_001.csv", ["review_needed", "translated"]),
    "NE_D": ("work/video/movies/subtitle_preview/NE_D_dialogue_rescan_draft_001.csv", ["review_needed", "translated"]),
    "OPEN": ("work/video/movies/subtitle_preview/OPEN_dialogue_rescan_draft_001.csv", ["review_needed", "translated"]),
}

VARIANTS = (
    {
        "name": "cheat",
        "base_report": "work/analysis/hangul_atlas_001_build.json",
        "build_id": "hangul_atlas_all_movies_cheat_001",
        "bin_name": "Rakugaki Showtime (Japan) [HANGUL-ATLAS-ALL-MOVIES-CHEAT-001].bin",
    },
    {
        "name": "no_cheat",
        "base_report": "work/analysis/hangul_atlas_no_cheat_001_build.json",
        "build_id": "hangul_atlas_all_movies_no_cheat_001",
        "bin_name": "Rakugaki Showtime (Japan) [HANGUL-ATLAS-ALL-MOVIES-NO-CHEAT-001].bin",
    },
)


def changed_indices(before: list[bytes], after: list[bytes]) -> list[int]:
    if len(before) != len(after):
        raise ValueError("candidate sector count differs from original")
    return [index for index, pair in enumerate(zip(before, after, strict=True)) if pair[0] != pair[1]]


def validate_movies(project: Path, source_bin: Path, catalog: dict) -> tuple[list[dict], set[int]]:
    by_name = {Path(item["disc_path"]).stem.upper(): item for item in catalog["movies"]}
    summaries: list[dict] = []
    all_movie_lbas: set[int] = set()
    for movie, (subtitle_rel, statuses) in MOVIE_INPUTS.items():
        record = by_name[movie]
        key = movie.lower()
        candidate = project / f"work/video/movies/psx_str_candidates/{movie}_full_subtitles_001.STR"
        donor_report_path = project / f"work/analysis/movie_psxavenc_donor_{key}_full_subtitles_001.json"
        merge_report_path = project / f"work/analysis/movie_subtitle_str_{key}_full_subtitles_001.json"
        decode_report_path = project / f"work/analysis/movie_subtitle_str_{key}_full_subtitles_001_decode.json"
        alignment_report_path = project / f"work/analysis/movie_subtitle_str_{key}_full_subtitles_001_alignment.json"
        subtitle_path = project / subtitle_rel
        original_path = project / record["output"]

        donor = load_json(donor_report_path)
        merge = load_json(merge_report_path)
        decode = load_json(decode_report_path)
        alignment = load_json(alignment_report_path)
        require_hash(subtitle_path, donor["input_subtitles_sha256"], f"{movie} subtitle input")
        if donor["included_statuses"] != statuses or donor["status"] != "ALIGNED_PSXAVENC_VIDEO_DONOR_STATIC_PASS":
            raise ValueError(f"{movie} donor status/filter contract differs")
        require_hash(project / donor["source_str"], donor["source_str_sha256"], f"{movie} donor source STR")
        require_hash(project / donor["source_media"], donor["source_media_sha256"], f"{movie} donor source media")
        require_hash(project / donor["donor_str"], donor["donor_str_sha256"], f"{movie} donor STR")
        require_hash(donor_report_path, merge["source_donor_report_sha256"], f"{movie} donor report link")
        require_hash(candidate, merge["output_sha256"], f"{movie} candidate")
        if merge["status"] != "STR_VIDEO_MERGE_STATIC_PASS_RUNTIME_NOT_BUILT":
            raise ValueError(f"{movie} merge status differs")
        if not merge["xa_byte_identical"] or not all(merge["validation"].values()):
            raise ValueError(f"{movie} merge validation failed")
        require_hash(merge_report_path, decode["merge_report_sha256"], f"{movie} decode merge link")
        if decode["status"] != "CANDIDATE_REVIEW_DECODE_PASS_RUNTIME_NOT_BUILT" or not all(decode["validation"].values()):
            raise ValueError(f"{movie} decode validation failed")
        if decode["input_sha256"] != merge["output_sha256"]:
            raise ValueError(f"{movie} decode candidate link differs")
        if alignment["status"] != "FRAME_ALIGNMENT_STATIC_PASS" or alignment["best_shift"] != 0:
            raise ValueError(f"{movie} alignment validation failed")
        if alignment["candidate_sha256"] != merge["output_sha256"]:
            raise ValueError(f"{movie} alignment candidate link differs")

        require_hash(original_path, record["raw_sha256"], f"{movie} extracted original")
        original_data = original_path.read_bytes()
        candidate_data = candidate.read_bytes()
        lba = int(record["iso_lba"])
        sector_count = int(record["sector_count"])
        if read_raw_extent(source_bin, lba, sector_count) != original_data:
            raise ValueError(f"{movie} source BIN extent differs from extracted original")
        original_sectors = split_sectors(original_data)
        candidate_sectors = split_sectors(candidate_data)
        changed = changed_indices(original_sectors, candidate_sectors)
        original_frames = collect_video_frames(original_sectors)
        candidate_frames = collect_video_frames(candidate_sectors)
        if sorted(original_frames) != sorted(candidate_frames):
            raise ValueError(f"{movie} candidate frame numbers differ")
        video_indices = {
            item["sector_index"]
            for chunks in original_frames.values()
            for item in chunks.values()
        }
        nonvideo_indices = set(range(sector_count)) - video_indices
        if not set(changed) <= video_indices:
            raise ValueError(f"{movie} changed a non-video sector")
        if any(candidate_sectors[i] != original_sectors[i] for i in nonvideo_indices):
            raise ValueError(f"{movie} changed XA/non-video data")
        if not all(verify_mode2_form1(candidate_sectors[i]) for i in video_indices):
            raise ValueError(f"{movie} has invalid Form 1 video EDC/ECC")
        for relative, (before, after) in enumerate(zip(original_sectors, candidate_sectors, strict=True)):
            target_lba = lba + relative
            if after[:24] != before[:24]:
                raise ValueError(f"{movie} changed raw header/subheader at LBA {target_lba}")
            if after[12:15] != expected_msf(target_lba) or after[15] != 2:
                raise ValueError(f"{movie} MSF/mode differs at LBA {target_lba}")
            if after[16:20] != after[20:24]:
                raise ValueError(f"{movie} duplicate subheader differs at LBA {target_lba}")
        movie_lbas = {lba + index for index in changed}
        if movie_lbas & all_movie_lbas:
            raise ValueError(f"{movie} overlaps an earlier movie write plan")
        all_movie_lbas |= movie_lbas
        summaries.append({
            "movie": movie,
            "lba_start": lba,
            "lba_end": lba + sector_count - 1,
            "sector_count": sector_count,
            "frame_count": len(original_frames),
            "video_sector_count": len(video_indices),
            "xa_sector_count": len(nonvideo_indices),
            "changed_video_sector_count": len(changed),
            "changed_relative_sectors": changed,
            "original": relative_path(original_path, project),
            "original_sha256": sha256_file(original_path),
            "candidate": relative_path(candidate, project),
            "candidate_sha256": sha256_file(candidate),
            "candidate_data": candidate_data,
            "subtitle": subtitle_rel,
            "subtitle_sha256": sha256_file(subtitle_path),
            "included_statuses": statuses,
            "cue_count": donor["cue_count"],
            "reports": {
                "donor": {"path": relative_path(donor_report_path, project), "sha256": sha256_file(donor_report_path)},
                "merge": {"path": relative_path(merge_report_path, project), "sha256": sha256_file(merge_report_path)},
                "decode": {"path": relative_path(decode_report_path, project), "sha256": sha256_file(decode_report_path)},
                "alignment": {"path": relative_path(alignment_report_path, project), "sha256": sha256_file(alignment_report_path)},
            },
        })
    return summaries, all_movie_lbas


def build_variant(
    project: Path,
    source_bin: Path,
    source_cue: Path,
    manifest_path: Path,
    catalog_path: Path,
    movies: list[dict],
    movie_lbas: set[int],
    variant: dict,
    force: bool,
) -> dict:
    base_report_path = project / variant["base_report"]
    base_report = load_json(base_report_path)
    if base_report["status"] != "STATIC_BUILD_PASS_RUNTIME_PENDING":
        raise ValueError(f"{variant['name']} base status differs")
    base_bin = project / base_report["output"]["bin"]
    require_hash(base_bin, base_report["output"]["bin_sha256"], f"{variant['name']} base BIN")
    base_writes = base_report["expected_writes"]["sectors"]
    base_lbas = {int(item["lba"]) for item in base_writes}
    if len(base_lbas) != len(base_writes):
        raise ValueError(f"{variant['name']} base has duplicate Expected Writes")
    if diff_image_sectors(source_bin, base_bin) != base_lbas:
        raise ValueError(f"{variant['name']} base diff differs from report")
    if base_lbas & movie_lbas:
        raise ValueError(f"{variant['name']} base overlaps movie writes")

    output_dir = project / "build" / variant["build_id"]
    output_bin = output_dir / variant["bin_name"]
    output_cue = output_bin.with_suffix(".cue")
    output_dir.mkdir(parents=True, exist_ok=True)
    if not force and (output_bin.exists() or output_cue.exists()):
        raise FileExistsError(f"output exists; pass --force to replace: {output_bin}")
    temporary = output_bin.with_suffix(output_bin.suffix + ".tmp")
    temporary_cue = output_cue.with_suffix(output_cue.suffix + ".tmp")
    for path in (temporary, temporary_cue):
        if path.exists():
            path.unlink()
    try:
        shutil.copyfile(source_bin, temporary)
        with base_bin.open("rb") as base, temporary.open("r+b") as output:
            for item in base_writes:
                lba = int(item["lba"])
                output.seek(lba * RAW_SECTOR_SIZE)
                if sha256_bytes(output.read(RAW_SECTOR_SIZE)) != item["expected_sha256"]:
                    raise ValueError(f"base Expected Write source mismatch at LBA {lba}")
                base.seek(lba * RAW_SECTOR_SIZE)
                final = base.read(RAW_SECTOR_SIZE)
                if sha256_bytes(final) != item["final_sha256"]:
                    raise ValueError(f"base Expected Write final mismatch at LBA {lba}")
                output.seek(lba * RAW_SECTOR_SIZE)
                output.write(final)
            for movie in movies:
                candidate_sectors = split_sectors(movie["candidate_data"])
                original_sectors = split_sectors((project / movie["original"]).read_bytes())
                for relative in movie["changed_relative_sectors"]:
                    lba = movie["lba_start"] + relative
                    output.seek(lba * RAW_SECTOR_SIZE)
                    if output.read(RAW_SECTOR_SIZE) != original_sectors[relative]:
                        raise ValueError(f"movie Expected Write source mismatch at LBA {lba}")
                    output.seek(lba * RAW_SECTOR_SIZE)
                    output.write(candidate_sectors[relative])
            output.flush()
            os.fsync(output.fileno())

        expected_lbas = base_lbas | movie_lbas
        actual_lbas = diff_image_sectors(source_bin, temporary)
        if actual_lbas != expected_lbas:
            raise ValueError(f"{variant['name']} final diff differs from combined write plan")
        for movie in movies:
            if read_raw_extent(temporary, movie["lba_start"], movie["sector_count"]) != movie["candidate_data"]:
                raise ValueError(f"{variant['name']} final {movie['movie']} extent differs")
        cue_text, count = re.subn(
            r'(?im)^FILE\s+"[^"]+"\s+BINARY$',
            f'FILE "{output_bin.name}" BINARY',
            source_cue.read_text(encoding="ascii"),
        )
        if count != 1:
            raise ValueError("expected exactly one CUE FILE line")
        with temporary_cue.open("w", encoding="ascii", newline="") as output:
            output.write(cue_text)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(output_bin)
        temporary_cue.replace(output_cue)
    finally:
        for path in (temporary, temporary_cue):
            if path.exists():
                path.unlink()

    public_movies = [{k: v for k, v in item.items() if k not in {"candidate_data", "changed_relative_sectors"}} for item in movies]
    report = {
        "schema_version": 1,
        "observation_id": variant["build_id"].upper(),
        "status": "STATIC_BUILD_PASS_DUCKSTATION_RUNTIME_PENDING",
        "source": {
            "manifest": relative_path(manifest_path, project),
            "manifest_sha256": sha256_file(manifest_path),
            "bin": relative_path(source_bin, project),
            "bin_sha256": sha256_file(source_bin),
            "cue": relative_path(source_cue, project),
            "cue_sha256": sha256_file(source_cue),
        },
        "base": {
            "variant": variant["name"],
            "report": relative_path(base_report_path, project),
            "report_sha256": sha256_file(base_report_path),
            "bin": relative_path(base_bin, project),
            "bin_sha256": sha256_file(base_bin),
            "expected_write_sector_count": len(base_lbas),
        },
        "movies": public_movies,
        "expected_writes": {
            "base_sector_count": len(base_lbas),
            "movie_sector_count": len(movie_lbas),
            "combined_sector_count": len(base_lbas | movie_lbas),
            "no_overlap": True,
        },
        "validation": {
            "all_report_hash_chains_verified": True,
            "all_seven_candidate_extents_equal_final_bin": True,
            "all_original_xa_sectors_byte_identical": True,
            "all_candidate_video_sectors_form1_edc_ecc_valid": True,
            "all_candidate_headers_msf_subheaders_match_original": True,
            "all_alignment_best_shifts_zero": True,
            "base_diff_equals_base_expected_writes": True,
            "final_diff_equals_combined_expected_writes": True,
            "output_size_preserved": output_bin.stat().st_size == source_bin.stat().st_size,
            "cue_points_to_output_bin": True,
        },
        "output": {
            "bin": relative_path(output_bin, project),
            "bin_sha256": sha256_file(output_bin),
            "cue": relative_path(output_cue, project),
            "cue_sha256": sha256_file(output_cue),
            "size": output_bin.stat().st_size,
        },
        "runtime_gate": "DuckStation fresh boot: verify all seven movies, XA sync, skip and completion transitions.",
    }
    report_path = project / "work/analysis" / f"{variant['build_id']}_build.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "variant": variant["name"],
        "base_writes": len(base_lbas),
        "movie_writes": len(movie_lbas),
        "combined_writes": len(base_lbas | movie_lbas),
        "output": report["output"],
        "report": relative_path(report_path, project),
    }, ensure_ascii=False, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    project = args.project.resolve()
    manifest_path = project / "workspace/config/source_manifest.json"
    catalog_path = project / "work/analysis/movie_catalog_001.json"
    manifest = load_json(manifest_path)
    catalog = load_json(catalog_path)
    source_bin = project / manifest["disc"]["bin"]
    source_cue = project / manifest["disc"]["cue"]
    require_hash(source_bin, manifest["disc"]["bin_sha256"], "source BIN")
    require_hash(source_cue, manifest["disc"]["cue_sha256"], "source CUE")
    if source_bin.stat().st_size != manifest["disc"]["bin_size"]:
        raise ValueError("source BIN size differs from manifest")
    if catalog["source"]["bin_sha256"] != manifest["disc"]["bin_sha256"]:
        raise ValueError("movie catalog source differs from manifest")
    movies, movie_lbas = validate_movies(project, source_bin, catalog)
    for variant in VARIANTS:
        build_variant(
            project, source_bin, source_cue, manifest_path, catalog_path,
            movies, movie_lbas, variant, args.force,
        )


if __name__ == "__main__":
    main()
