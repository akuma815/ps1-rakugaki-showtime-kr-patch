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
from faster_whisper.audio import decode_audio
from faster_whisper.utils import _MODELS


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MOVIE_NAME = "GOOD"
AUDIO_PATH = PROJECT_ROOT / "work" / "audio" / "movies" / "GOOD.wav"
MODEL_ROOT = PROJECT_ROOT / "work" / "tools" / "faster-whisper-models"
OUTPUT_PATH = PROJECT_ROOT / "work" / "analysis" / "GOOD_dialogue_rescan_001.json"
FPS = 15.0
WINDOW_SECONDS = 8.0
HOP_SECONDS = 4.0
USER_REPORTED_SECONDS = (21.0, 58.0, 71.0, 83.0, 88.0)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def wav_info(path: Path) -> dict:
    with wave.open(str(path), "rb") as audio:
        frame_count = audio.getnframes()
        sample_rate = audio.getframerate()
        return {
            "channels": audio.getnchannels(),
            "sample_width_bytes": audio.getsampwidth(),
            "sample_rate_hz": sample_rate,
            "sample_frame_count": frame_count,
            "duration_seconds": frame_count / sample_rate,
        }


def frame_floor(seconds: float) -> int:
    return math.floor(seconds * FPS)


def frame_ceil_exclusive(seconds: float) -> int:
    return math.ceil(seconds * FPS)


def write_progress(report: dict) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    info = wav_info(AUDIO_PATH)
    duration = float(info["duration_seconds"])
    window_starts = []
    cursor = 0.0
    while cursor < duration:
        window_starts.append(cursor)
        cursor += HOP_SECONDS

    options = {
        "language": "ja",
        "task": "transcribe",
        "beam_size": 5,
        "best_of": 5,
        "patience": 1.0,
        "temperature": 0.0,
        "condition_on_previous_text": False,
        "word_timestamps": True,
        "vad_filter": False,
        "compression_ratio_threshold": 2.4,
        "log_prob_threshold": -2.0,
        "no_speech_threshold": 1.0,
    }
    runs = (
        ("large_v3_turbo_window8_hop4_no_vad", "large-v3-turbo"),
        ("small_window8_hop4_no_vad", "small"),
    )
    report = {
        "schema_version": 1,
        "observation_id": f"RAKUGAKI-{MOVIE_NAME}-DIALOGUE-RESCAN-001",
        "status": "MACHINE_EXHAUSTIVE_RESCAN_IN_PROGRESS",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "asset_id": f"MOVIE-{MOVIE_NAME}",
            "fps": FPS,
            "window_seconds": WINDOW_SECONDS,
            "hop_seconds": HOP_SECONDS,
            "user_reported_missing_seconds": list(USER_REPORTED_SECONDS),
            "reason": "The earlier whole-file inventory used VAD and omitted most voices mixed with music and effects.",
            "policy": "Preserve all machine candidates. Exact Japanese, speaker, and subtitle inclusion require listening review.",
        },
        "audio": {
            "path": AUDIO_PATH.relative_to(PROJECT_ROOT).as_posix(),
            "size_bytes": AUDIO_PATH.stat().st_size,
            "sha256": sha256_file(AUDIO_PATH),
            "wav": info,
        },
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "faster_whisper": faster_whisper.__version__,
            "ctranslate2": ctranslate2.__version__,
            "device": "cpu",
            "compute_type": "int8",
            "cpu_threads": 16,
            "model_root": MODEL_ROOT.relative_to(PROJECT_ROOT).as_posix(),
            "options": options,
            "runs": [
                {
                    "run_id": run_id,
                    "model_alias": model_alias,
                    "model_repository": _MODELS[model_alias],
                }
                for run_id, model_alias in runs
            ],
        },
        "runs": [],
        "review": {
            "classification": "pending_machine_rescan",
            "human_review_required": True,
        },
    }
    write_progress(report)

    audio = decode_audio(str(AUDIO_PATH), sampling_rate=16000)
    for run_id, model_alias in runs:
        print(f"loading {model_alias}", flush=True)
        load_started = time.perf_counter()
        model = WhisperModel(
            model_alias,
            device="cpu",
            compute_type="int8",
            cpu_threads=16,
            num_workers=1,
            download_root=str(MODEL_ROOT),
        )
        run_record = {
            "run_id": run_id,
            "model_alias": model_alias,
            "model_load_seconds": round(time.perf_counter() - load_started, 6),
            "windows": [],
            "status": "MACHINE_WINDOWS_IN_PROGRESS",
        }
        report["runs"].append(run_record)
        write_progress(report)

        for window_index, start in enumerate(window_starts):
            end = min(start + WINDOW_SECONDS, duration)
            sample_start = round(start * 16000)
            sample_end = round(end * 16000)
            chunk = audio[sample_start:sample_end]
            started = time.perf_counter()
            segments_iter, transcribe_info = model.transcribe(chunk, **options)
            segments = []
            for segment in segments_iter:
                absolute_start = start + float(segment.start)
                absolute_end = start + float(segment.end)
                words = []
                for word in segment.words or []:
                    word_start = start + float(word.start)
                    word_end = start + float(word.end)
                    words.append(
                        {
                            "start_seconds": word_start,
                            "end_seconds": word_end,
                            "start_frame_floor": frame_floor(word_start),
                            "end_frame_ceil_exclusive": frame_ceil_exclusive(word_end),
                            "word": word.word,
                            "probability": float(word.probability),
                        }
                    )
                segments.append(
                    {
                        "start_seconds": absolute_start,
                        "end_seconds": absolute_end,
                        "start_frame_floor": frame_floor(absolute_start),
                        "end_frame_ceil_exclusive": frame_ceil_exclusive(absolute_end),
                        "text": segment.text.strip(),
                        "avg_logprob": float(segment.avg_logprob),
                        "compression_ratio": float(segment.compression_ratio),
                        "no_speech_prob": float(segment.no_speech_prob),
                        "words": words,
                    }
                )
            window_record = {
                "window_index": window_index,
                "window_start_seconds": start,
                "window_end_seconds": end,
                "contains_user_reported_seconds": [
                    anchor for anchor in USER_REPORTED_SECONDS if start <= anchor < end
                ],
                "language": transcribe_info.language,
                "language_probability": float(transcribe_info.language_probability),
                "segments": segments,
                "full_text": " ".join(item["text"] for item in segments),
                "transcribe_seconds": round(time.perf_counter() - started, 6),
            }
            run_record["windows"].append(window_record)
            write_progress(report)
            print(
                f"{run_id} {start:06.2f}-{end:06.2f}: "
                f"{window_record['full_text']}",
                flush=True,
            )
        run_record["status"] = "MACHINE_WINDOWS_COMPLETE_HUMAN_REVIEW_PENDING"
        write_progress(report)
        del model

    report["status"] = "MACHINE_EXHAUSTIVE_RESCAN_COMPLETE_HUMAN_REVIEW_PENDING"
    report["review"] = {
        "classification": "machine_candidates_need_human_listening",
        "human_review_required": True,
        "notes": [
            "Overlapping windows intentionally duplicate candidates so a boundary cannot silently remove speech.",
            "Music, effects, screams, and stylized voices can produce repeated or hallucinated text.",
            "Do not promote any candidate into the approved subtitle SSOT without bilingual listening review.",
        ],
    }
    write_progress(report)
    print(f"wrote {OUTPUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
