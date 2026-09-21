from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import tempfile

from scoreplayer.musicxml import parse_musicxml
from scoreplayer.diagnostics import analyze_score
from scoreplayer.synth import render_wav
from scoreplayer.midi import export_midi
from scoreplayer.omr import homr_available


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-engine", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    fixture = root / "fixtures" / "simple.musicxml"
    score = parse_musicxml(fixture)

    checks = {
        "musicxml": bool(score.events),
        "repeat_route": score.measure_order[:5] == [1, 2, 1, 2, 3],
        "chord": len([e for e in score.events if abs(e.beat) < 1e-9]) >= 3,
        "diagnostics": isinstance(analyze_score(score), list),
    }

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        checks["wav"] = render_wav(score, tmp / "test.wav", bpm_override=120, sample_rate=8000).exists()
        checks["midi"] = export_midi(score, tmp / "test.mid", bpm=120).exists()

    engine_ok, engine_msg = homr_available()
    checks["omr_engine"] = engine_ok

    result = {
        "version": "0.8.0",
        "checks": checks,
        "engine": engine_msg,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

    required = [v for k, v in checks.items() if k != "omr_engine"]
    if not all(required):
        raise SystemExit(2)
    if args.require_engine and not engine_ok:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
