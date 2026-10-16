#!/usr/bin/env python3
"""Render frame-based Korean subtitle cues onto review MP4s.

CSV intervals are zero-based and half-open: start_frame is included and
end_frame is excluded. ASS timestamps are derived preview data; CSV frame
numbers remain authoritative for later PS1 MDEC reinsertion.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

from decode_psx_movies import make_contact_sheet


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


CSV_FIELDS = [
    "subtitle_id",
    "movie",
    "start_frame",
    "end_frame",
    "speaker",
    "japanese",
    "korean",
    "status",
    "notes",
]
DEFAULT_RENDER_STATUSES = {"translated", "approved"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ass_timestamp(frame: int, fps: int) -> str:
    """Convert a frame boundary to ASS centiseconds without crossing it."""
    if frame < 0 or fps <= 0:
        raise ValueError("frame must be non-negative and fps must be positive")
    centiseconds = frame * 100 // fps
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    seconds, fraction = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{fraction:02d}"


def ass_text(value: str) -> str:
    """Escape user text while preserving explicit ASS line breaks (\\N)."""
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\N")
    parts = normalized.split("\\N")
    escaped = [
        part.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
        for part in parts
    ]
    return "\\N".join(escaped)


def ffmpeg_filter_path(path: Path) -> str:
    value = path.resolve().as_posix()
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


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


def load_cues(
    path: Path,
    movie_frames: dict[str, int],
    included_statuses: set[str],
    font: ImageFont.FreeTypeFont,
    max_width: int,
    max_lines: int,
    stroke_width: int,
) -> dict[str, list[dict]]:
    cues: dict[str, list[dict]] = defaultdict(list)
    seen_ids: set[str] = set()
    measure = ImageDraw.Draw(Image.new("L", (1, 1)))
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames != CSV_FIELDS:
            raise ValueError(f"subtitle CSV fields differ: {reader.fieldnames} != {CSV_FIELDS}")
        for row_number, row in enumerate(reader, start=2):
            subtitle_id = row["subtitle_id"].strip()
            if not subtitle_id:
                raise ValueError(f"row {row_number}: subtitle_id is empty")
            if subtitle_id in seen_ids:
                raise ValueError(f"row {row_number}: duplicate subtitle_id {subtitle_id}")
            seen_ids.add(subtitle_id)
            movie = row["movie"].strip().upper()
            if movie not in movie_frames:
                raise ValueError(f"row {row_number}: unknown movie {movie}")
            try:
                start_frame = int(row["start_frame"])
                end_frame = int(row["end_frame"])
            except ValueError as error:
                raise ValueError(f"row {row_number}: frame values must be integers") from error
            if start_frame < 0 or end_frame <= start_frame:
                raise ValueError(f"row {row_number}: invalid half-open interval {start_frame}:{end_frame}")
            if end_frame > movie_frames[movie]:
                raise ValueError(
                    f"row {row_number}: end_frame {end_frame} exceeds {movie} frame count {movie_frames[movie]}"
                )
            status = row["status"].strip().lower()
            if status not in included_statuses:
                continue
            korean = row["korean"].strip()
            if not korean:
                raise ValueError(f"row {row_number}: included cue has no Korean text")
            lines = korean.replace("\r\n", "\n").replace("\r", "\n").replace("\\N", "\n").split("\n")
            if len(lines) > max_lines:
                raise ValueError(f"row {row_number}: {len(lines)} subtitle lines exceed the {max_lines}-line limit")
            for line in lines:
                bounds = measure.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
                if bounds[2] - bounds[0] > max_width:
                    raise ValueError(f"row {row_number}: subtitle line exceeds safe width: {line}")
            cues[movie].append({
                **row,
                "movie": movie,
                "start_frame": start_frame,
                "end_frame": end_frame,
                "status": status,
                "korean": korean,
            })
    for movie, movie_cues in cues.items():
        movie_cues.sort(key=lambda item: (item["start_frame"], item["end_frame"], item["subtitle_id"]))
        for previous, current in zip(movie_cues, movie_cues[1:]):
            if current["start_frame"] < previous["end_frame"]:
                raise ValueError(
                    f"overlapping cues in {movie}: {previous['subtitle_id']} and {current['subtitle_id']}"
                )
    return cues


def build_ass(cues: list[dict], style: dict) -> str:
    fps = int(style["frame_rate"])
    width = int(style["play_resolution"]["width"])
    height = int(style["play_resolution"]["height"])
    lines = [
        "[Script Info]",
        "; Generated from the frame-authoritative Rakugaki Showtime subtitle CSV.",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
            "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
            "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
        ),
        (
            f"Style: Default,{style['font_family']},{style['font_size']},{style['primary_colour']},"
            f"&H000000FF,{style['outline_colour']},{style['back_colour']},0,0,0,0,100,100,0,0,"
            f"{style['border_style']},{style['outline']},{style['shadow']},{style['alignment']},"
            f"{style['margin_left']},{style['margin_right']},{style['margin_vertical']},1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue in cues:
        lines.append(
            "Dialogue: 0,"
            f"{ass_timestamp(cue['start_frame'], fps)},{ass_timestamp(cue['end_frame'], fps)},"
            f"Default,{ass_text(cue['speaker'])},0,0,0,,{ass_text(cue['korean'])}"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--input", type=Path, default=Path("workspace/text/rakugaki_movie_subtitles_ja_ko.csv"))
    parser.add_argument("--style", type=Path, default=Path("workspace/config/movie_subtitle_style.json"))
    parser.add_argument("--movie", action="append", help="render only the named movie stem")
    parser.add_argument("--include-status", action="append", help="status to render; repeat as needed")
    parser.add_argument("--crf", type=int, default=10)
    args = parser.parse_args()

    project = args.project.resolve()
    input_path = args.input if args.input.is_absolute() else project / args.input
    style_path = args.style if args.style.is_absolute() else project / args.style
    style = json.loads(style_path.read_text(encoding="utf-8"))
    catalog_path = project / "work/analysis/movie_catalog_001.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    decode_report_path = project / "work/analysis/movie_decode_001.json"
    decode_report = json.loads(decode_report_path.read_text(encoding="utf-8"))
    decoded_frames = {item["asset_id"].removeprefix("MOVIE-"): item["decoded_frame_count"] for item in decode_report["movies"]}
    movie_records = {Path(item["disc_path"]).stem.upper(): item for item in catalog["movies"]}
    movie_frames = {name: item["video"]["frame_count"] for name, item in movie_records.items()}
    selected = {name.upper() for name in args.movie} if args.movie else set(movie_records)
    missing = selected - set(movie_records)
    if missing:
        raise ValueError(f"movie names not found: {', '.join(sorted(missing))}")

    font_path = Path(style["font_path"])
    if not font_path.is_absolute():
        font_path = (project / font_path).resolve()
    if not font_path.is_file():
        raise FileNotFoundError(font_path)
    font = ImageFont.truetype(str(font_path), int(style["font_size"]))
    included_statuses = (
        {status.lower() for status in args.include_status}
        if args.include_status
        else DEFAULT_RENDER_STATUSES
    )
    safe_width = (
        int(style["play_resolution"]["width"])
        - int(style["margin_left"])
        - int(style["margin_right"])
    )
    cues_by_movie = load_cues(
        input_path,
        movie_frames,
        included_statuses,
        font,
        safe_width,
        int(style["max_lines"]),
        int(style["outline"]),
    )

    ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    output_dir = project / "work/video/movies/subtitle_preview"
    output_dir.mkdir(parents=True, exist_ok=True)
    frame_aligned_review_sources = {
        "GOOD": project / "work/video/movies/cross_decoder/GOOD_full_jpsxdec_aligned/GOOD.STR[0].avi",
    }
    records = []
    for movie in sorted(selected):
        cues = cues_by_movie.get(movie, [])
        if not cues:
            print(f"skipped {movie}: no included subtitle cues")
            continue
        aligned_source = frame_aligned_review_sources.get(movie)
        source = (
            aligned_source
            if aligned_source is not None and aligned_source.is_file()
            else project / f"work/video/movies/review_mp4/{movie}.mp4"
        )
        if not source.is_file():
            raise FileNotFoundError(source)
        canonical_input = (project / "workspace/text/rakugaki_movie_subtitles_ja_ko.csv").resolve()
        input_stem = input_path.stem
        output_stem = (
            movie
            if input_path.resolve() == canonical_input
            else input_stem if input_stem.upper().startswith(f"{movie}_") else f"{movie}_{input_stem}"
        )
        ass_path = output_dir / f"{output_stem}_subtitles.ass"
        ass_path.write_text(build_ass(cues, style), encoding="utf-8-sig")
        target = output_dir / f"{output_stem}_subtitle_preview.mp4"
        filter_value = (
            f"ass=filename='{ffmpeg_filter_path(ass_path)}':"
            f"fontsdir='{ffmpeg_filter_path(font_path.parent)}'"
        )
        command = [
            str(ffmpeg), "-hide_banner", "-loglevel", "warning", "-y", "-threads", "1",
            "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?", "-vf", filter_value,
            "-c:v", "libx264", "-preset", "medium", "-crf", str(args.crf), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart", str(target),
        ]
        encode_log = run_checked(command)
        sheet_path = output_dir / f"{output_stem}_subtitle_preview.png"
        sheet = make_contact_sheet(target, sheet_path, sample_count=12)
        expected_frames = movie_frames[movie] if source == aligned_source else decoded_frames[movie]
        if sheet["frame_count"] != expected_frames:
            raise ValueError(
                f"subtitle preview frame mismatch for {movie}: {sheet['frame_count']} != "
                f"{expected_frames}"
            )
        records.append({
            "movie": movie,
            "cue_count": len(cues),
            "source": source.relative_to(project).as_posix(),
            "source_sha256": sha256_file(source),
            "source_frame_policy": (
                "raw-frame-aligned independent decoder review proxy"
                if source == aligned_source
                else "FFmpeg review proxy"
            ),
            "audio_policy": "AAC 192 kbps, 48 kHz review audio for broad MP4 player compatibility",
            "ass": ass_path.relative_to(project).as_posix(),
            "ass_sha256": sha256_file(ass_path),
            "output": target.relative_to(project).as_posix(),
            "output_sha256": sha256_file(target),
            "contact_sheet": {
                **sheet,
                "path": sheet_path.relative_to(project).as_posix(),
            },
            "ffmpeg_warnings": [line for line in encode_log.splitlines() if line.strip()],
        })
        print(f"rendered {movie}: {len(cues)} cues, {sheet['frame_count']} frames")

    rendered_cues = [
        cue
        for movie in selected
        for cue in cues_by_movie.get(movie, [])
    ]
    if not records:
        report_status = "NO_RENDERABLE_CUES"
    elif (
        str(style.get("status", "")).startswith("STYLE_USER_APPROVED")
        and rendered_cues
        and all(cue["status"] == "approved" for cue in rendered_cues)
    ):
        report_status = "APPROVED_SUBTITLE_PREVIEW_RENDERED"
    elif str(style.get("status", "")).startswith("STYLE_USER_APPROVED"):
        report_status = "DRAFT_SUBTITLE_PREVIEW_RENDERED_USER_TEXT_APPROVAL_PENDING"
    else:
        report_status = "STYLE_PREVIEW_RENDERED_USER_APPROVAL_PENDING"

    report = {
        "schema_version": 1,
        "observation_id": "MOVIE-SUBTITLE-PREVIEW-001",
        "frame_contract": "zero-based half-open [start_frame, end_frame); CSV frames are authoritative",
        "input": input_path.relative_to(project).as_posix(),
        "input_sha256": sha256_file(input_path),
        "included_statuses": sorted(included_statuses),
        "style": style_path.relative_to(project).as_posix(),
        "style_sha256": sha256_file(style_path),
        "style_status": style.get("status"),
        "source_decode_report": decode_report_path.relative_to(project).as_posix(),
        "source_decode_report_sha256": sha256_file(decode_report_path),
        "font": str(font_path),
        "font_sha256": sha256_file(font_path),
        "movies": records,
        "status": report_status,
    }
    canonical_input = (project / "workspace/text/rakugaki_movie_subtitles_ja_ko.csv").resolve()
    report_name = (
        "movie_subtitle_preview_001.json"
        if input_path.resolve() == canonical_input
        else f"movie_subtitle_preview_{input_path.stem}.json"
    )
    report_path = project / "work/analysis" / report_name
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "movies": len(records),
        "report": report_path.relative_to(project).as_posix(),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
