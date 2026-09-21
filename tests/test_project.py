from pathlib import Path
from PIL import Image

from scoreplayer.model import NoteEvent, Score, TempoMark
from scoreplayer.project import save_project, load_project


def test_project_roundtrip(tmp_path):
    img = tmp_path / "page.png"
    Image.new("RGB", (20, 20), "white").save(img)
    score = Score(
        events=[NoteEvent(0, 1, 61, measure=1, source_order=3)],
        tempos=[TempoMark(0, 96)],
        measure_order=[1],
    )
    project = save_project(tmp_path / "demo.scoreplayer", score, [img])
    loaded, pages, xml, report = load_project(project, tmp_path / "extract")
    assert loaded.events[0].midi == 61
    assert len(pages) == 1 and Path(pages[0]).exists()
    assert xml is None
