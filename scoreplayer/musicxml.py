from __future__ import annotations
import math
import xml.etree.ElementTree as ET
from pathlib import Path

from scoreplayer.model import NoteEvent, Score, TempoMark
from scoreplayer.navigation import expand_measure_order


STEP_TO_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _children(node: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in node if _local(c.tag) == name]


def _child(node: ET.Element, name: str) -> ET.Element | None:
    for c in node:
        if _local(c.tag) == name:
            return c
    return None


def _text(node: ET.Element | None, default: str = "") -> str:
    if node is None or node.text is None:
        return default
    return node.text.strip()


def pitch_to_midi(note: ET.Element) -> int | None:
    pitch = _child(note, "pitch")
    if pitch is None:
        return None
    step = _text(_child(pitch, "step"))
    octave_s = _text(_child(pitch, "octave"))
    if step not in STEP_TO_SEMITONE or not octave_s:
        return None
    alter_s = _text(_child(pitch, "alter"), "0")
    try:
        octave = int(octave_s)
        alter = int(float(alter_s))
    except ValueError:
        return None
    return (octave + 1) * 12 + STEP_TO_SEMITONE[step] + alter


def _find_tempo(measure: ET.Element, default: float) -> float:
    for node in measure.iter():
        if _local(node.tag) == "sound" and "tempo" in node.attrib:
            try:
                return float(node.attrib["tempo"])
            except ValueError:
                pass
        if _local(node.tag) == "per-minute" and node.text:
            try:
                return float(node.text.strip())
            except ValueError:
                pass
    return default


def _partwise_root(path: str | Path) -> ET.Element:
    root = ET.parse(path).getroot()
    if _local(root.tag) != "score-partwise":
        raise ValueError("当前版本仅支持 score-partwise MusicXML。")
    return root


def parse_musicxml(path: str | Path) -> Score:
    root = _partwise_root(path)

    parts = [p for p in root if _local(p.tag) == "part"]
    if not parts:
        raise ValueError("MusicXML 中没有找到 part。")

    # Navigation is normally shared by piano staves. Use the first part as authority.
    first_measures = [m for m in parts[0] if _local(m.tag) == "measure"]
    order, nav_warnings = expand_measure_order(first_measures)

    if not order:
        order = list(range(len(first_measures)))

    all_events: list[NoteEvent] = []
    tempo_marks: list[TempoMark] = []
    absolute_beat = 0.0
    source_order = 0

    # To preserve playback order after repeats, parse measure occurrences in the expanded order.
    for occurrence_idx, measure_idx in enumerate(order):
        measure_duration = 0.0
        occurrence_events: list[NoteEvent] = []
        occurrence_tempos: list[TempoMark] = []

        for part_index, part in enumerate(parts):
            measures = [m for m in part if _local(m.tag) == "measure"]
            if measure_idx >= len(measures):
                continue
            measure = measures[measure_idx]

            divisions = 1.0
            cursor = 0.0
            max_cursor = 0.0
            last_onset_by_voice: dict[str, float] = {}
            current_bpm = tempo_marks[-1].bpm if tempo_marks else 120.0

            attrs = _child(measure, "attributes")
            if attrs is not None:
                div = _child(attrs, "divisions")
                if div is not None and _text(div):
                    try:
                        divisions = max(1.0, float(_text(div)))
                    except ValueError:
                        divisions = 1.0

            bpm = _find_tempo(measure, current_bpm)
            if part_index == 0 and (not tempo_marks or abs(bpm - current_bpm) > 1e-9):
                occurrence_tempos.append(TempoMark(absolute_beat, bpm))
            elif part_index == 0 and not tempo_marks and not occurrence_tempos:
                occurrence_tempos.append(TempoMark(absolute_beat, bpm))

            for node in measure:
                tag = _local(node.tag)

                if tag == "attributes":
                    div = _child(node, "divisions")
                    if div is not None and _text(div):
                        try:
                            divisions = max(1.0, float(_text(div)))
                        except ValueError:
                            pass
                    continue

                if tag == "backup":
                    dur = _child(node, "duration")
                    try:
                        cursor -= float(_text(dur, "0")) / divisions
                    except ValueError:
                        pass
                    cursor = max(0.0, cursor)
                    continue

                if tag == "forward":
                    dur = _child(node, "duration")
                    try:
                        cursor += float(_text(dur, "0")) / divisions
                    except ValueError:
                        pass
                    max_cursor = max(max_cursor, cursor)
                    continue

                if tag != "note":
                    continue

                duration_node = _child(node, "duration")
                try:
                    duration = float(_text(duration_node, "0")) / divisions
                except ValueError:
                    duration = 0.0

                voice = _text(_child(node, "voice"), "1")
                staff_s = _text(_child(node, "staff"), "1")
                try:
                    staff = int(staff_s)
                except ValueError:
                    staff = 1

                is_chord = _child(node, "chord") is not None
                is_grace = _child(node, "grace") is not None

                if is_chord:
                    onset = last_onset_by_voice.get(voice, cursor)
                else:
                    onset = cursor
                    last_onset_by_voice[voice] = onset

                midi = pitch_to_midi(node)
                is_rest = _child(node, "rest") is not None

                if midi is not None and not is_rest:
                    occurrence_events.append(
                        NoteEvent(
                            beat=absolute_beat + onset,
                            duration_beats=max(duration, 0.08 if is_grace else 0.0),
                            midi=midi,
                            part=part.attrib.get("id", f"P{part_index+1}"),
                            staff=staff,
                            voice=voice,
                            measure=measure_idx + 1,
                            source_order=source_order,
                        )
                    )
                    source_order += 1

                if not is_chord and not is_grace:
                    cursor += duration
                    max_cursor = max(max_cursor, cursor)

            measure_duration = max(measure_duration, max_cursor)

        all_events.extend(occurrence_events)
        tempo_marks.extend(occurrence_tempos)
        absolute_beat += max(measure_duration, 0.0)

    all_events.sort(key=lambda e: (e.beat, e.source_order, e.midi))
    tempo_marks.sort(key=lambda t: t.beat)

    # de-duplicate identical tempo marks at the same beat
    deduped_tempos: list[TempoMark] = []
    for t in tempo_marks or [TempoMark(0.0, 120.0)]:
        if deduped_tempos and abs(deduped_tempos[-1].beat - t.beat) < 1e-9:
            deduped_tempos[-1] = t
        else:
            deduped_tempos.append(t)

    return Score(
        events=all_events,
        tempos=deduped_tempos,
        measure_order=[x + 1 for x in order],
        warnings=nav_warnings,
    )


def beat_to_seconds(score: Score, beat: float, bpm_override: float | None = None) -> float:
    if bpm_override and bpm_override > 0:
        return beat * 60.0 / bpm_override

    tempos = sorted(score.tempos, key=lambda t: t.beat)
    if not tempos:
        return beat * 0.5

    total = 0.0
    last_beat = 0.0
    current_bpm = tempos[0].bpm

    for mark in tempos[1:]:
        if beat <= mark.beat:
            break
        total += (mark.beat - last_beat) * 60.0 / current_bpm
        last_beat = mark.beat
        current_bpm = mark.bpm

    total += max(0.0, beat - last_beat) * 60.0 / current_bpm
    return total


def event_times_seconds(score: Score, bpm_override: float | None = None) -> list[tuple[NoteEvent, float, float]]:
    result = []
    for event in score.events:
        start = beat_to_seconds(score, event.beat, bpm_override)
        end = beat_to_seconds(score, event.beat + event.duration_beats, bpm_override)
        result.append((event, start, max(0.03, end - start)))
    return result
