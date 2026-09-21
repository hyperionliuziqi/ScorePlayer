from __future__ import annotations
import json
import shutil
import zipfile
from pathlib import Path

from scoreplayer.model import NoteEvent, Score, TempoMark

PROJECT_VERSION = 1


def score_to_dict(score: Score) -> dict:
    return {
        "events": [
            {
                "beat": e.beat,
                "duration_beats": e.duration_beats,
                "midi": e.midi,
                "velocity": e.velocity,
                "part": e.part,
                "staff": e.staff,
                "voice": e.voice,
                "measure": e.measure,
                "source_order": e.source_order,
            }
            for e in score.events
        ],
        "tempos": [{"beat": t.beat, "bpm": t.bpm} for t in score.tempos],
        "measure_order": list(score.measure_order),
        "warnings": list(score.warnings),
    }


def score_from_dict(data: dict) -> Score:
    return Score(
        events=[NoteEvent(**e) for e in data.get("events", [])],
        tempos=[TempoMark(**t) for t in data.get("tempos", [{"beat": 0.0, "bpm": 120.0}])],
        measure_order=list(data.get("measure_order", [])),
        warnings=list(data.get("warnings", [])),
    )


def save_project(
    path: str | Path,
    score: Score,
    images: list[str | Path] | None = None,
    source_musicxml: str | Path | None = None,
    preprocess_report: list[dict] | None = None,
) -> Path:
    """Save a self-contained .scoreplayer project (ZIP container)."""
    path = Path(path)
    if path.suffix.lower() != ".scoreplayer":
        path = path.with_suffix(".scoreplayer")
    path.parent.mkdir(parents=True, exist_ok=True)

    images = [Path(p) for p in (images or [])]
    manifest = {
        "project_version": PROJECT_VERSION,
        "score": score_to_dict(score),
        "pages": [],
        "musicxml": None,
        "preprocess_report": preprocess_report or [],
    }

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for idx, image in enumerate(images, 1):
            if not image.exists():
                continue
            safe_name = image.name.replace("/", "_").replace("\\", "_")
            arc = f"pages/{idx:03d}_{safe_name}"
            z.write(image, arc)
            manifest["pages"].append(arc)

        if source_musicxml:
            source_musicxml = Path(source_musicxml)
            if source_musicxml.exists():
                arc = "source/recognized.musicxml"
                z.write(source_musicxml, arc)
                manifest["musicxml"] = arc

        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    return path


def load_project(path: str | Path, extract_dir: str | Path) -> tuple[Score, list[str], str | None, list[dict]]:
    path = Path(path)
    extract_dir = Path(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(path, "r") as z:
        manifest = json.loads(z.read("manifest.json").decode("utf-8"))
        if int(manifest.get("project_version", 0)) != PROJECT_VERSION:
            raise ValueError("不支持的 ScorePlayer 工程版本。")

        project_root = extract_dir / path.stem
        if project_root.exists():
            shutil.rmtree(project_root)
        project_root.mkdir(parents=True, exist_ok=True)
        z.extractall(project_root)

    score = score_from_dict(manifest["score"])
    images = [str(project_root / arc) for arc in manifest.get("pages", [])]
    xml_arc = manifest.get("musicxml")
    xml_path = str(project_root / xml_arc) if xml_arc else None
    return score, images, xml_path, list(manifest.get("preprocess_report", []))
