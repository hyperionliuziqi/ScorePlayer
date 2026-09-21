from __future__ import annotations
import json
import tempfile
from pathlib import Path

import cv2
import numpy as np

from scoreplayer.diagnostics import analyze_score
from scoreplayer.midi import export_midi
from scoreplayer.layout import analyze_page
from scoreplayer.musicxml import parse_musicxml
from scoreplayer.omr import homr_available, recognize_pages
from scoreplayer.synth import render_wav


SMOKE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>4</divisions><staves>2</staves></attributes>
      <direction><sound tempo="120"/></direction>
      <barline location="left"><repeat direction="forward"/></barline>
      <note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice><staff>1</staff></note>
      <note><chord/><pitch><step>E</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice><staff>1</staff></note>
      <note><chord/><pitch><step>G</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice><staff>1</staff></note>
    </measure>
    <measure number="2">
      <note><pitch><step>D</step><alter>1</alter><octave>4</octave></pitch><duration>4</duration><voice>1</voice><staff>1</staff></note>
      <barline location="right"><repeat direction="backward" times="2"/></barline>
    </measure>
    <measure number="3">
      <note><pitch><step>F</step><octave>4</octave></pitch><duration>8</duration><voice>1</voice><staff>1</staff></note>
    </measure>
  </part>
</score-partwise>
"""


def run_self_test(
    require_engine: bool = False,
    omr_smoke_image: str | None = None,
    report_path: str | None = None,
) -> int:
    result: dict = {"version": "0.8.0", "checks": {}, "details": {}}

    with tempfile.TemporaryDirectory(prefix="scoreplayer_selftest_") as td:
        td = Path(td)
        xml = td / "smoke.musicxml"
        xml.write_text(SMOKE_XML, encoding="utf-8")

        score = parse_musicxml(xml)
        result["checks"]["musicxml"] = bool(score.events)
        result["checks"]["repeat_route"] = score.measure_order[:5] == [1, 2, 1, 2, 3]
        result["checks"]["chord"] = len([e for e in score.events if abs(e.beat) < 1e-9]) >= 3
        result["checks"]["diagnostics"] = isinstance(analyze_score(score), list)

        wav = render_wav(score, td / "smoke.wav", bpm_override=120, sample_rate=8000)
        mid = export_midi(score, td / "smoke.mid", bpm=120)
        result["checks"]["wav"] = wav.exists() and wav.stat().st_size > 1000
        result["checks"]["midi"] = mid.exists() and mid.read_bytes().startswith(b"MThd")

        # Verify the page-follow geometry that v0.8 uses to highlight measures.
        layout_image = np.full((520, 900, 3), 255, dtype=np.uint8)
        for staff_y in (120, 212):
            for line in range(5):
                y = staff_y + line * 8
                cv2.line(layout_image, (80, y), (820, y), (0, 0, 0), 2)
        for x in (80, 330, 575, 820):
            cv2.line(layout_image, (x, 120), (x, 244), (0, 0, 0), 2)
        layout_path = td / "layout-smoke.png"
        cv2.imwrite(str(layout_path), layout_image)
        page_geometry = analyze_page(layout_path)
        result["checks"]["layout_systems"] = len(page_geometry.systems) == 1
        result["checks"]["layout_measures"] = (
            bool(page_geometry.systems)
            and page_geometry.systems[0].detected_measure_count == 3
        )

        engine_ok, engine_msg = homr_available()
        result["checks"]["omr_engine_import"] = engine_ok
        result["details"]["engine"] = engine_msg

        if omr_smoke_image:
            try:
                omr_out = td / "omr_smoke.musicxml"
                recognize_pages([omr_smoke_image], omr_out)
                result["checks"]["omr_offline_inference"] = (
                    omr_out.exists() and omr_out.stat().st_size > 100
                )
                result["details"]["omr_output_size"] = omr_out.stat().st_size if omr_out.exists() else 0
            except Exception as exc:
                result["checks"]["omr_offline_inference"] = False
                result["details"]["omr_error"] = str(exc)

    core_keys = ["musicxml", "repeat_route", "chord", "diagnostics", "wav", "midi", "layout_systems", "layout_measures"]
    ok = all(result["checks"].get(k, False) for k in core_keys)

    if require_engine:
        ok = ok and result["checks"].get("omr_engine_import", False)
        if omr_smoke_image:
            ok = ok and result["checks"].get("omr_offline_inference", False)

    result["passed"] = bool(ok)

    if report_path:
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        Path(report_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    return 0 if ok else 3
