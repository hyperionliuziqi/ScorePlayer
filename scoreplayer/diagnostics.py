from __future__ import annotations
from dataclasses import dataclass
from collections import defaultdict

from scoreplayer.model import Score, NoteEvent


@dataclass
class Diagnostic:
    severity: str   # info / warning / error
    code: str
    message: str
    measure: int | None = None
    beat: float | None = None
    midi: int | None = None


def analyze_score(score: Score) -> list[Diagnostic]:
    out: list[Diagnostic] = []

    if not score.events:
        return [Diagnostic("error", "NO_NOTES", "没有解析到任何可播放音符。")]

    # 1. Piano range sanity check.
    for e in score.events:
        if e.midi < 21 or e.midi > 108:
            out.append(Diagnostic(
                "warning",
                "PIANO_RANGE",
                f"{e.pitch_name} 超出标准 88 键钢琴范围，可能是 OMR 八度识别错误。",
                e.measure, e.beat, e.midi
            ))

    # 2. Extremely short / long note values.
    for e in score.events:
        if 0 < e.duration_beats < 1/32:
            out.append(Diagnostic(
                "warning", "TINY_DURATION",
                f"{e.pitch_name} 的时值异常短（{e.duration_beats:.4f} 拍）。",
                e.measure, e.beat, e.midi
            ))
        if e.duration_beats > 16:
            out.append(Diagnostic(
                "warning", "LONG_DURATION",
                f"{e.pitch_name} 的时值异常长（{e.duration_beats:.2f} 拍）。",
                e.measure, e.beat, e.midi
            ))

    # Group simultaneous notes.
    by_beat: dict[float, list[NoteEvent]] = defaultdict(list)
    for e in score.events:
        by_beat[round(e.beat, 6)].append(e)

    for beat, group in by_beat.items():
        if len(group) > 12:
            out.append(Diagnostic(
                "warning", "DENSE_CHORD",
                f"同一时刻检测到 {len(group)} 个音，超过常见钢琴和弦密度，建议检查。",
                group[0].measure, beat
            ))
        mids = sorted(e.midi for e in group)
        if mids and mids[-1] - mids[0] > 60 and len(group) >= 4:
            out.append(Diagnostic(
                "warning", "WIDE_CHORD",
                f"同一和弦跨度达到 {mids[-1]-mids[0]} 个半音，可能存在八度误识别。",
                group[0].measure, beat
            ))

    # 3. Large melodic leaps within each part/staff/voice.
    tracks: dict[tuple[str, int, str], list[NoteEvent]] = defaultdict(list)
    for e in score.events:
        tracks[(e.part, e.staff, e.voice)].append(e)

    for key, events in tracks.items():
        events.sort(key=lambda e: (e.beat, e.source_order))
        last: NoteEvent | None = None
        for e in events:
            if last is not None and e.beat > last.beat + 1e-6:
                leap = abs(e.midi - last.midi)
                if leap >= 24:
                    out.append(Diagnostic(
                        "warning", "OCTAVE_LEAP",
                        f"{last.pitch_name} → {e.pitch_name} 跳跃 {leap} 个半音；若原谱没有大跳，可能是 OMR 八度错误。",
                        e.measure, e.beat, e.midi
                    ))
            last = e

    # 4. Exact duplicate notes at the same beat, same part/staff/voice.
    seen = set()
    for e in score.events:
        key = (round(e.beat, 6), round(e.duration_beats, 6), e.midi, e.part, e.staff, e.voice)
        if key in seen:
            out.append(Diagnostic(
                "warning", "DUPLICATE_NOTE",
                f"{e.pitch_name} 在同一声部同一时刻重复出现，可能是重复识别。",
                e.measure, e.beat, e.midi
            ))
        seen.add(key)

    return out
