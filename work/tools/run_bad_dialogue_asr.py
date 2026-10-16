from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import platform
import sys
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

import ctranslate2
import faster_whisper
from faster_whisper import WhisperModel
from faster_whisper.utils import _MODELS


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INPUT_PATH = PROJECT_ROOT / "work" / "audio" / "BAD_dialogue_f166_283.wav"
OUTPUT_PATH = PROJECT_ROOT / "work" / "analysis" / "BAD_dialogue_asr_review.json"
MODEL_ROOT = PROJECT_ROOT / "work" / "tools" / "faster-whisper-models"

SOURCE_START_SECONDS = 11.0
SOURCE_END_SECONDS = 19.0
VIDEO_FPS = 15.0
REVIEW_FRAME_START = 166
REVIEW_FRAME_END = 283

COMMON_OPTIONS = {
    "language": "ja",
    "task": "transcribe",
    "temperature": 0.0,
    "condition_on_previous_text": False,
    "word_timestamps": True,
    "without_timestamps": False,
    "suppress_blank": True,
    "suppress_tokens": [-1],
    "compression_ratio_threshold": 2.4,
    "log_prob_threshold": -1.0,
    "no_speech_threshold": 0.6,
    "log_progress": True,
}

RUNS = [
    {
        "run_id": "small_beam5_no_vad",
        "model": "small",
        "options": {
            **COMMON_OPTIONS,
            "beam_size": 5,
            "best_of": 5,
            "patience": 1.0,
            "vad_filter": False,
        },
    },
    {
        "run_id": "large_v3_turbo_beam5_no_vad",
        "model": "large-v3-turbo",
        "options": {
            **COMMON_OPTIONS,
            "beam_size": 5,
            "best_of": 5,
            "patience": 1.0,
            "vad_filter": False,
        },
    },
    {
        "run_id": "large_v3_turbo_greedy_vad",
        "model": "large-v3-turbo",
        "options": {
            **COMMON_OPTIONS,
            "beam_size": 1,
            "best_of": 1,
            "patience": 1.0,
            "vad_filter": True,
            "vad_parameters": {
                "min_silence_duration_ms": 250,
                "speech_pad_ms": 200,
            },
        },
    },
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def source_seconds(clip_seconds: float) -> float:
    return round(SOURCE_START_SECONDS + clip_seconds, 6)


def frame_floor(seconds: float) -> int:
    return math.floor(seconds * VIDEO_FPS)


def frame_ceil_exclusive(seconds: float) -> int:
    return math.ceil(seconds * VIDEO_FPS)


def serialize_word(word) -> dict:
    start = float(word.start)
    end = float(word.end)
    source_start = source_seconds(start)
    source_end = source_seconds(end)
    return {
        "start_seconds_clip": start,
        "end_seconds_clip": end,
        "start_seconds_source": source_start,
        "end_seconds_source": source_end,
        "start_frame_floor": frame_floor(source_start),
        "end_frame_ceil_exclusive": frame_ceil_exclusive(source_end),
        "word": word.word,
        "probability": float(word.probability),
    }


def serialize_segment(segment) -> dict:
    start = float(segment.start)
    end = float(segment.end)
    source_start = source_seconds(start)
    source_end = source_seconds(end)
    return {
        "id": int(segment.id),
        "seek": int(segment.seek),
        "start_seconds_clip": start,
        "end_seconds_clip": end,
        "start_seconds_source": source_start,
        "end_seconds_source": source_end,
        "start_frame_floor": frame_floor(source_start),
        "end_frame_ceil_exclusive": frame_ceil_exclusive(source_end),
        "text": segment.text,
        "tokens": [int(token) for token in segment.tokens],
        "temperature": float(segment.temperature),
        "avg_logprob": float(segment.avg_logprob),
        "avg_token_probability_estimate": math.exp(float(segment.avg_logprob)),
        "compression_ratio": float(segment.compression_ratio),
        "no_speech_prob": float(segment.no_speech_prob),
        "words": [serialize_word(word) for word in (segment.words or [])],
    }


def serialize_info(info) -> dict:
    return {
        "language": info.language,
        "language_probability": float(info.language_probability),
        "duration_seconds": float(info.duration),
        "duration_after_vad_seconds": float(info.duration_after_vad),
        "all_language_probs": info.all_language_probs,
    }


def wav_metadata(path: Path) -> dict:
    with wave.open(str(path), "rb") as audio:
        frame_count = audio.getnframes()
        sample_rate = audio.getframerate()
        return {
            "channels": audio.getnchannels(),
            "sample_width_bytes": audio.getsampwidth(),
            "sample_rate_hz": sample_rate,
            "sample_frame_count": frame_count,
            "duration_seconds": frame_count / sample_rate,
            "compression_type": audio.getcomptype(),
        }


def main() -> None:
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    result = {
        "schema_version": 1,
        "artifact_id": "rakugaki_showtime_bad_dialogue_asr_review_001",
        "status": "asr_draft_needs_human_review",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "asset_id": "BAD",
            "purpose": "Independent draft transcription comparison; not approved source text.",
            "input_path_project_relative": INPUT_PATH.relative_to(PROJECT_ROOT).as_posix(),
            "source_time_range_seconds": [SOURCE_START_SECONDS, SOURCE_END_SECONDS],
            "review_frame_range_inclusive": [REVIEW_FRAME_START, REVIEW_FRAME_END],
            "video_fps": VIDEO_FPS,
            "frame_mapping_note": (
                "ASR timestamps are clip-relative and are also shifted by +11.0 s. "
                "Frame fields are floor/ceil projections at 15 fps, not human-aligned subtitle decisions. "
                "The WAV includes nominal f165 padding while the requested review range begins at f166."
            ),
        },
        "input": {
            "size_bytes": INPUT_PATH.stat().st_size,
            "sha256": sha256_file(INPUT_PATH),
            "wav": wav_metadata(INPUT_PATH),
        },
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "processor": platform.processor(),
            "device": "cpu",
            "compute_type": "int8",
            "cpu_threads": 16,
            "packages": {
                "faster-whisper": faster_whisper.__version__,
                "ctranslate2": ctranslate2.__version__,
                "av": package_version("av"),
                "huggingface-hub": package_version("huggingface-hub"),
                "tokenizers": package_version("tokenizers"),
                "numpy": package_version("numpy"),
            },
            "official_upstream": "https://github.com/SYSTRAN/faster-whisper",
            "model_download_root_project_relative": MODEL_ROOT.relative_to(PROJECT_ROOT).as_posix(),
        },
        "runs": [],
        "comparison": {},
        "review": {
            "classification": "pending_machine_runs",
            "human_review_required": True,
            "notes": [],
        },
    }

    loaded_model_name = None
    model = None

    for run in RUNS:
        model_name = run["model"]
        entry = {
            "run_id": run["run_id"],
            "model_alias": model_name,
            "resolved_model_repository": _MODELS.get(model_name, model_name),
            "device": "cpu",
            "compute_type": "int8",
            "cpu_threads": 16,
            "options": run["options"],
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
        }

        try:
            if loaded_model_name != model_name:
                del model
                load_start = time.perf_counter()
                model = WhisperModel(
                    model_name,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=16,
                    num_workers=1,
                    download_root=str(MODEL_ROOT),
                )
                entry["model_load_seconds"] = round(time.perf_counter() - load_start, 6)
                loaded_model_name = model_name
            else:
                entry["model_load_seconds"] = 0.0
                entry["model_reused_from_previous_run"] = True

            transcribe_start = time.perf_counter()
            segments_iter, info = model.transcribe(str(INPUT_PATH), **run["options"])
            segments = [serialize_segment(segment) for segment in segments_iter]
            entry["transcribe_seconds"] = round(time.perf_counter() - transcribe_start, 6)
            entry["info"] = serialize_info(info)
            entry["segments"] = segments
            entry["full_text"] = "".join(segment["text"] for segment in segments).strip()
            entry["segment_count"] = len(segments)
            entry["word_count"] = sum(len(segment["words"]) for segment in segments)
            entry["status"] = "completed"
        except Exception as exc:
            entry["status"] = "failed"
            entry["error_type"] = type(exc).__name__
            entry["error"] = str(exc)

        entry["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        result["runs"].append(entry)

        # Preserve progress if a later, larger model fails or is interrupted.
        OUTPUT_PATH.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    completed = [run for run in result["runs"] if run["status"] == "completed"]
    texts = {run["run_id"]: run["full_text"] for run in completed}
    result["comparison"] = {
        "completed_run_count": len(completed),
        "full_text_by_run": texts,
        "all_full_texts_exact_match": len(set(texts.values())) <= 1 if texts else False,
        "pairwise_exact_match": {
            f"{left['run_id']}__vs__{right['run_id']}": left["full_text"] == right["full_text"]
            for index, left in enumerate(completed)
            for right in completed[index + 1 :]
        },
    }
    result["review"] = {
        "classification": "machine_draft_needs_human_review",
        "human_review_required": True,
        "notes": [
            "Forced language=ja does not validate the recognized wording.",
            "avg_logprob and word probability are model scores, not proof of lexical correctness.",
            "Music, effects, character voice, and the short eight-second window can cause hallucination or unstable segmentation.",
            "Any disagreement among model/configuration outputs must remain unresolved until bilingual listening review.",
        ],
    }
    OUTPUT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
