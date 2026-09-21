from scoreplayer.model import Score, NoteEvent
from scoreplayer.diagnostics import analyze_score


def test_flags_large_leap():
    score = Score(events=[
        NoteEvent(0, 1, 60, staff=1, voice="1", measure=1),
        NoteEvent(1, 1, 96, staff=1, voice="1", measure=1),
    ])
    codes = [d.code for d in analyze_score(score)]
    assert "OCTAVE_LEAP" in codes


def test_flags_duplicate():
    e1 = NoteEvent(0, 1, 60, part="P1", staff=1, voice="1", measure=1)
    e2 = NoteEvent(0, 1, 60, part="P1", staff=1, voice="1", measure=1)
    codes = [d.code for d in analyze_score(Score(events=[e1, e2]))]
    assert "DUPLICATE_NOTE" in codes
