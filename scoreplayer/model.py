from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class NoteEvent:
    beat: float
    duration_beats: float
    midi: int
    velocity: int = 84
    part: str = ""
    staff: int = 1
    voice: str = "1"
    measure: int = 0
    source_order: int = 0

    @property
    def pitch_name(self) -> str:
        names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
        octave = self.midi // 12 - 1
        return f"{names[self.midi % 12]}{octave}"


@dataclass
class TempoMark:
    beat: float
    bpm: float


@dataclass
class Score:
    events: list[NoteEvent] = field(default_factory=list)
    tempos: list[TempoMark] = field(default_factory=lambda: [TempoMark(0.0, 120.0)])
    measure_order: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_beats(self) -> float:
        return max((e.beat + e.duration_beats for e in self.events), default=0.0)
