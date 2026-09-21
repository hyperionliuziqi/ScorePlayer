from pathlib import Path
from scoreplayer.musicxml import parse_musicxml, event_times_seconds


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "simple.musicxml"


def test_chord_and_two_staff_parse():
    score = parse_musicxml(FIXTURE)
    assert len(score.events) >= 7

    beat0 = [e for e in score.events if abs(e.beat) < 1e-9]
    mids = sorted(e.midi for e in beat0)
    assert 60 in mids and 64 in mids and 67 in mids  # C major chord


def test_repeat_expands_measure_order():
    score = parse_musicxml(FIXTURE)
    # fixture repeats measures 1-2 before measure 3
    assert score.measure_order[:5] == [1, 2, 1, 2, 3]


def test_event_times_monotonic():
    score = parse_musicxml(FIXTURE)
    timed = event_times_seconds(score, bpm_override=120)
    starts = [x[1] for x in timed]
    assert starts == sorted(starts)
