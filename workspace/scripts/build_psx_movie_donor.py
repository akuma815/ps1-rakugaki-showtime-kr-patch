#!/usr/bin/env python3
"""Build a lossless subtitle master and an aligned psxavenc STR donor."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
from PIL import ImageFont

from decode_psx_movies import sha256_file
from merge_psx_movie_video import collect_video_frames, select_donor_frame_pairs, split_sectors
from render_movie_subtitle_preview import (
    DEFAULT_RENDER_STATUSES,
    build_ass,
    ffmpeg_filter_path,
    load_cues,
)


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def relative_path(path: Path, project: Path) -> str:
    try:
        return path.relative_to(project).as_posix()
    except ValueError:
        return str(path)


def run_checked(command: list[str]) -> str:
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}\n{result.stdout}")
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--movie", required=True)
    parser.add_argument("--input", type=Path, default=Path("workspace/text/rakugaki_movie_subtitles_ja_ko.csv"))
    parser.add_argument(
        "--source-media",
        type=Path,
        help="optional decoded A/V source; raw STR remains authoritative for sector/frame layout",
    )
    parser.add_argument("--style", type=Path, default=Path("workspace/config/movie_subtitle_style.json"))
    parser.add_argument("--encoder-config", type=Path, default=Path("workspace/config/movie_psxavenc.json"))
    parser.add_argument("--include-status", action="append")
    parser.add_argument("--tag", default="001")
    parser.add_argument(
        "--tail-padding-frames",
        type=int,
        help="override codec-specific lossless-master tail padding",
    )
    args = parser.parse_args()

    project = args.project.resolve()

    def resolve(path: Path) -> Path:
        return path if path.is_absolute() else project / path

    movie = args.movie.upper()
    input_path = resolve(args.input)
    style_path = resolve(args.style)
    encoder_config_path = resolve(args.encoder_config)
    style = json.loads(style_path.read_text(encoding="utf-8"))
    encoder_config = json.loads(encoder_config_path.read_text(encoding="utf-8"))
    catalog_path = project / "work/analysis/movie_catalog_001.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    movie_records = {Path(item["disc_path"]).stem.upper(): item for item in catalog["movies"]}
    if movie not in movie_records:
        raise ValueError(f"movie not found: {movie}")
    if movie not in encoder_config["movie_codecs"]:
        raise ValueError(f"movie codec not configured: {movie}")
    record = movie_records[movie]

    font_path = Path(style["font_path"])
    if not font_path.is_absolute():
        font_path = (project / font_path).resolve()
    font = ImageFont.truetype(str(font_path), int(style["font_size"]))
    statuses = (
        {status.lower() for status in args.include_status}
        if args.include_status
        else DEFAULT_RENDER_STATUSES
    )
    movie_frames = {name: item["video"]["frame_count"] for name, item in movie_records.items()}
    safe_width = (
        int(style["play_resolution"]["width"])
        - int(style["margin_left"])
        - int(style["margin_right"])
    )
    cues = load_cues(
        input_path,
        movie_frames,
        statuses,
        font,
        safe_width,
        int(style["max_lines"]),
        int(style["outline"]),
    ).get(movie, [])
    if not cues:
        raise ValueError(f"no included subtitle cues for {movie}")

    source = project / record["output"]
    if sha256_file(source) != record["raw_sha256"]:
        raise ValueError(f"raw STR hash mismatch: {source}")
    media_source = resolve(args.source_media) if args.source_media else source
    if not media_source.is_file():
        raise FileNotFoundError(media_source)
    tool = project / encoder_config["tool"]["local_executable"]
    if sha256_file(tool) != encoder_config["tool"]["local_executable_sha256"]:
        raise ValueError("psxavenc executable hash mismatch")

    tag = args.tag.replace(" ", "_")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", tag):
        raise ValueError("tag may contain only letters, numbers, underscores and hyphens")
    ass_path = project / f"work/video/movies/psxavenc/{movie}_{tag}.ass"
    master_path = project / f"work/video/movies/subtitle_master/{movie}_{tag}_master.mkv"
    donor_path = project / f"work/video/movies/psxavenc/{movie}_{tag}_donor.str"
    for path in (ass_path, master_path, donor_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    ass_path.write_text(build_ass(cues, style), encoding="utf-8-sig")

    common = encoder_config["common"]
    alignment = encoder_config["alignment"]
    frame_count = int(record["video"]["frame_count"])
    frame_rate = int(common["frame_rate"])
    sample_rate = int(common["sample_rate"])
    tail_frames = (
        int(args.tail_padding_frames)
        if args.tail_padding_frames is not None
        else int(alignment["tail_padding_frames"])
    )
    if tail_frames < 0:
        raise ValueError("tail padding frames cannot be negative")
    if sample_rate % frame_rate:
        raise ValueError("sample rate must be divisible by frame rate for exact padding")
    total_audio_samples = (frame_count + tail_frames) * (sample_rate // frame_rate)

    ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    filter_graph = (
        f"[0:v]ass=filename='{ffmpeg_filter_path(ass_path)}':"
        f"fontsdir='{ffmpeg_filter_path(font_path.parent)}',"
        f"tpad=stop={tail_frames}:stop_mode=clone[v];"
        f"[0:a]aresample={sample_rate},apad=whole_len={total_audio_samples}[a]"
    )
    master_command = [
        str(ffmpeg), "-hide_banner", "-loglevel", "warning", "-y", "-threads", "1",
    ]
    if args.source_media is None:
        master_command.extend(["-f", "psxstr"])
    master_command.extend([
        "-i", str(media_source), "-filter_complex", filter_graph,
        "-map", "[v]", "-map", "[a]", "-c:v", "ffv1", "-level", "3", "-pix_fmt", "yuv420p",
        "-r", str(frame_rate), "-fps_mode", "cfr", "-c:a", "pcm_s16le", "-ar", str(sample_rate),
        "-ac", str(common["channels"]), "-frames:v", str(frame_count + tail_frames), "-shortest",
        str(master_path),
    ])
    master_log = run_checked(master_command)
    master_frames, master_duration = imageio_ffmpeg.count_frames_and_secs(str(master_path))
    if master_frames != frame_count + tail_frames:
        raise ValueError(f"lossless master frame mismatch: {master_frames} != {frame_count + tail_frames}")

    codec = encoder_config["movie_codecs"][movie]
    donor_command = [
        str(tool), "-q", "-t", common["format"], "-v", codec,
        "-f", str(common["sample_rate"]), "-b", str(common["bits_per_sample"]),
        "-c", str(common["channels"]), "-F", str(common["file_number"]),
        "-C", str(common["channel_number"]), "-s", f"{common['width']}x{common['height']}",
        "-r", str(common["frame_rate"]), "-x", str(common["cd_speed"]),
        "-T", common["video_type_id"], "-X", str(master_path), str(donor_path),
    ]
    donor_log = run_checked(donor_command)
    original_frames = collect_video_frames(split_sectors(source.read_bytes()))
    donor_frames = collect_video_frames(split_sectors(donor_path.read_bytes()))
    pairs = select_donor_frame_pairs(
        original_frames,
        donor_frames,
        int(alignment["drop_leading_donor_frames"]),
    )
    if len(pairs) != frame_count:
        raise ValueError(f"aligned donor frame mismatch: {len(pairs)} != {frame_count}")

    report = {
        "schema_version": 1,
        "observation_id": "MOVIE-PSXAVENC-DONOR-001",
        "movie": movie,
        "tag": tag,
        "input_subtitles": relative_path(input_path, project),
        "input_subtitles_sha256": sha256_file(input_path),
        "included_statuses": sorted(statuses),
        "cue_count": len(cues),
        "source_str": relative_path(source, project),
        "source_str_sha256": sha256_file(source),
        "source_media": relative_path(media_source, project),
        "source_media_sha256": sha256_file(media_source),
        "subtitle_ass": relative_path(ass_path, project),
        "subtitle_ass_sha256": sha256_file(ass_path),
        "lossless_master": relative_path(master_path, project),
        "lossless_master_size": master_path.stat().st_size,
        "lossless_master_sha256": sha256_file(master_path),
        "lossless_master_frames": master_frames,
        "lossless_master_duration": round(master_duration, 6),
        "donor_str": relative_path(donor_path, project),
        "donor_str_size": donor_path.stat().st_size,
        "donor_str_sha256": sha256_file(donor_path),
        "donor_frames": len(donor_frames),
        "codec": codec,
        "alignment": {
            **alignment,
            "effective_tail_padding_frames": tail_frames,
            "selected_donor_frame_min": min(item[1] for item in pairs),
            "selected_donor_frame_max": max(item[1] for item in pairs),
            "aligned_frame_count": len(pairs),
        },
        "tool_config": relative_path(encoder_config_path, project),
        "tool_config_sha256": sha256_file(encoder_config_path),
        "tool_executable_sha256": sha256_file(tool),
        "ffmpeg_sha256": sha256_file(ffmpeg),
        "commands": {
            "lossless_master": master_command[1:],
            "psxavenc_donor": donor_command[1:],
        },
        "warnings": {
            "lossless_master": [line for line in master_log.splitlines() if line.strip()],
            "psxavenc": [line for line in donor_log.splitlines() if line.strip()],
        },
        "policy": encoder_config["policy"],
        "status": "ALIGNED_PSXAVENC_VIDEO_DONOR_STATIC_PASS",
    }
    report_path = project / f"work/analysis/movie_psxavenc_donor_{movie.lower()}_{tag}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "movie": movie,
        "frames": frame_count,
        "donor_frames": len(donor_frames),
        "donor": report["donor_str"],
        "report": relative_path(report_path, project),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
