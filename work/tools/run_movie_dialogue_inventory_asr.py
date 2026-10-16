from __future__ import annotations

import hashlib
import json
import math
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
AUDIO_ROOT = PROJECT_ROOT / "work" / "audio" / "movies"
MODEL_ROOT = PROJECT_ROOT / "work" / "tools" / "faster-whisper-models"
OUTPUT_PATH = PROJECT_ROOT / "work" / "analysis" / "movie_dialogue_asr_001.json"
MOVIES = ("GOOD", "NE_A", "NE_B", "NE_C", "NE_D", "OPEN")
FPS = 15.0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def wav_info(path: Path) -> dict:
    with wave.open(str(path), "rb") as audio:
        frames = audio.getnframes()
        rate = audio.getframerate()
        return {
            "channels": audio.getnchannels(),
            "sample_width_bytes": audio.getsampwidth(),
            "sample_rate_hz": rate,
            "sample_frame_count": frames,
            "duration_seconds": frames / rate,
        }


def frame_floor(seconds: float) -> int:
    return math.floor(seconds * FPS)


def frame_ceil_exclusive(seconds: float) -> int:
    return math.ceil(seconds * FPS)


def serialize_word(word) -> dict:
    return {
        "start_seconds": float(word.start),
        "end_seconds": float(word.end),
        "start_frame_floor": frame_floor(float(word.start)),
        "end_frame_ceil_exclusive": frame_ceil_exclusive(float(word.end)),
        "word": word.word,
        "probability": float(word.probability),
    }


def serialize_segment(segment) -> dict:
    return {
        "id": int(segment.id),
        "seek": int(segment.seek),
        "start_seconds": float(segment.start),
        "end_seconds": float(segment.end),
        "start_frame_floor": frame_floor(float(segment.start)),
        "end_frame_ceil_exclusive": frame_ceil_exclusive(float(segment.end)),
        "text": segment.text.strip(),
        "avg_logprob": float(segment.avg_logprob),
        "compression_ratio": float(segment.compression_ratio),
        "no_speech_prob": float(segment.no_speech_prob),
        "words": [serialize_word(word) for word in (segment.words or [])],
    }


def write_progress(report: dict) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    common_options = {
        "language": "ja",
        "task": "transcribe",
        "beam_size": 5,
        "best_of": 5,
        "patience": 1.0,
        "temperature": 0.0,
        "condition_on_previous_text": False,
        "word_timestamps": True,
        "vad_filter": True,
        "vad_parameters": {
            "min_silence_duration_ms": 250,
            "speech_pad_ms": 200,
        },
        "compression_ratio_threshold": 2.4,
        "log_prob_threshold": -1.0,
        "no_speech_threshold": 0.6,
    }
    runs = (
        ("large_v3_turbo_beam5_vad", "large-v3-turbo"),
        ("small_beam5_vad", "small"),
    )
    report = {
        "schema_version": 1,
        "observation_id": "RAKUGAKI-MOVIE-DIALOGUE-ASR-001",
        "status": "MACHINE_INVENTORY_IN_PROGRESS",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "movies": list(MOVIES),
            "excluded_already_approved": ["BAD"],
            "fps": FPS,
            "purpose": "Machine-first dialogue timing inventory; Japanese text and speaker identity require human listening review.",
            "good_timing_warning": "Use raw frame numbers. The old FFmpeg GOOD proxy drops raw frame 1609; use the 1863-frame jPSXdec aligned review AVI instead.",
        },
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "faster_whisper": faster_whisper.__version__,
            "ctranslate2": ctranslate2.__version__,
            "device": "cpu",
            "compute_type": "int8",
            "cpu_threads": 16,
            "runs": [
                {
                    "run_id": run_id,
                    "model_alias": model_alias,
                    "model_repository": _MODELS[model_alias],
                }
                for run_id, model_alias in runs
            ],
            "model_root": MODEL_ROOT.relative_to(PROJECT_ROOT).as_posix(),
            "options": common_options,
        },
        "movies": [
            {
                "asset_id": f"MOVIE-{movie}",
                "audio": {
                    "path": (AUDIO_ROOT / f"{movie}.wav").relative_to(PROJECT_ROOT).as_posix(),
                    "size_bytes": (AUDIO_ROOT / f"{movie}.wav").stat().st_size,
                    "sha256": sha256_file(AUDIO_ROOT / f"{movie}.wav"),
                    "wav": wav_info(AUDIO_ROOT / f"{movie}.wav"),
                },
                "runs": [],
                "status": "MACHINE_RUNS_PENDING",
            }
            for movie in MOVIES
        ],
        "review": {
            "classification": "pending_machine_inventory",
            "human_review_required": True,
        },
    }
    write_progress(report)

    for run_id, model_alias in runs:
        load_started = time.perf_counter()
        model = WhisperModel(
            model_alias,
            device="cpu",
            compute_type="int8",
            cpu_threads=16,
            num_workers=1,
            download_root=str(MODEL_ROOT),
        )
        load_seconds = round(time.perf_counter() - load_started, 6)
        for movie, movie_record in zip(MOVIES, report["movies"], strict=True):
            source = AUDIO_ROOT / f"{movie}.wav"
            print(f"ASR {run_id} {movie}: starting", flush=True)
            started = time.perf_counter()
            segments_iter, info = model.transcribe(str(source), **common_options)
            segments = [serialize_segment(segment) for segment in segments_iter]
            run_record = {
                "run_id": run_id,
                "model_alias": model_alias,
                "model_load_seconds": load_seconds,
                "language": info.language,
                "language_probability": float(info.language_probability),
                "duration_after_vad_seconds": float(info.duration_after_vad),
                "segments": segments,
                "full_text": " ".join(item["text"] for item in segments),
                "segment_count": len(segments),
                "transcribe_seconds": round(time.perf_counter() - started, 6),
                "status": "MACHINE_DRAFT_NEEDS_HUMAN_LISTENING",
            }
            movie_record["runs"].append(run_record)
            write_progress(report)
            print(
                f"ASR {run_id} {movie}: {len(segments)} candidates in "
                f"{run_record['transcribe_seconds']:.1f}s",
                flush=True,
            )
        del model

    for movie_record in report["movies"]:
        texts = {run["run_id"]: run["full_text"] for run in movie_record["runs"]}
        movie_record["comparison"] = {
            "full_text_by_run": texts,
            "all_full_texts_exact_match": len(set(texts.values())) == 1,
        }
        movie_record["status"] = "MACHINE_DRAFT_NEEDS_HUMAN_LISTENING"

    report["status"] = "MACHINE_DIALOGUE_INVENTORY_COMPLETE_HUMAN_REVIEW_PENDING"
    report["review"] = {
        "classification": "machine_draft_needs_human_review",
        "human_review_required": True,
        "notes": [
            "Music, effects, stylized voices, and songs can create hallucinated Japanese text.",
            "Do not copy machine text into the approved subtitle SSOT without bilingual listening review.",
            "Frame fields are floor/ceil projections at 15 fps and are not final subtitle in/out decisions.",
        ],
    }
    write_progress(report)
    print(f"wrote {OUTPUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
