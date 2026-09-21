from scoreplayer.edit import ScoreEditSession
from scoreplayer.model import NoteEvent, Score


def make_score():
    return Score(events=[
        NoteEvent(0, 1, 60, measure=1, source_order=0),
        NoteEvent(1, 1, 64, measure=1, source_order=1),
    ])


def test_transpose_undo_redo():
    score = make_score()
    s = ScoreEditSession(score)
    assert s.transpose(0, 12).changed
    assert sorted(e.midi for e in score.events) == [64, 72]
    assert s.undo().changed
    assert [e.midi for e in score.events] == [60, 64]
    assert s.redo().changed
    assert sorted(e.midi for e in score.events) == [64, 72]


def test_delete_and_duration():
    score = make_score()
    s = ScoreEditSession(score)
    assert s.scale_duration(0, .5).changed
    assert abs(score.events[0].duration_beats - .5) < 1e-9
    assert s.delete(0).changed
    assert len(score.events) == 1
