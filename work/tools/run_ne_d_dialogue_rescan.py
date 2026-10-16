"""Run the established overlapping-window, VAD-off dialogue scan for NE_D."""

from pathlib import Path

import run_good_dialogue_rescan as worker


if __name__ == "__main__":
    project = Path(__file__).resolve().parents[2]
    worker.MOVIE_NAME = "NE_D"
    worker.AUDIO_PATH = project / "work" / "audio" / "movies" / "NE_D.wav"
    worker.OUTPUT_PATH = project / "work" / "analysis" / "NE_D_dialogue_rescan_001.json"
    worker.USER_REPORTED_SECONDS = ()
    worker.main()
