from pathlib import Path
from scoreplayer.musicxml import parse_musicxml
from scoreplayer.synth import render_wav
from scoreplayer.midi import export_midi


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "simple.musicxml"


def test_render_wav_and_midi(tmp_path):
    score = parse_musicxml(FIXTURE)
    wav = render_wav(score, tmp_path / "out.wav", bpm_override=120, sample_rate=8000)
    midi = export_midi(score, tmp_path / "out.mid", bpm=120)
    assert wav.exists() and wav.stat().st_size > 1000
    assert midi.read_bytes().startswith(b"MThd")
