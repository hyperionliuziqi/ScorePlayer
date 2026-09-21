from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass

from scoreplayer.model import NoteEvent, Score


@dataclass
class EditResult:
    changed: bool
    message: str = ""


class ScoreEditSession:
    """Small, deterministic undo/redo layer around Score.events.

    The event list is intentionally snapshotted because a typical OMR score contains
    only a few thousand NoteEvent dataclasses, which keeps the implementation simple
    and reliable for desktop use.
    """

    def __init__(self, score: Score):
        self.score = score
        self._baseline = deepcopy(score.events)
        self._undo: list[list[NoteEvent]] = []
        self._redo: list[list[NoteEvent]] = []

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def _checkpoint(self):
        self._undo.append(deepcopy(self.score.events))
        if len(self._undo) > 80:
            self._undo.pop(0)
        self._redo.clear()

    def _normalize(self):
        self.score.events.sort(key=lambda e: (e.beat, e.source_order, e.midi))

    def transpose(self, index: int, semitones: int) -> EditResult:
        if not (0 <= index < len(self.score.events)):
            return EditResult(False, "没有选中有效音符。")
        event = self.score.events[index]
        new_midi = max(0, min(127, event.midi + semitones))
        if new_midi == event.midi:
            return EditResult(False, "音高已经到达 MIDI 边界。")
        self._checkpoint()
        event.midi = new_midi
        self._normalize()
        return EditResult(True, f"音高已调整 {semitones:+d} 半音。")

    def set_midi(self, index: int, midi: int) -> EditResult:
        if not (0 <= index < len(self.score.events)):
            return EditResult(False, "没有选中有效音符。")
        midi = max(0, min(127, int(midi)))
        if self.score.events[index].midi == midi:
            return EditResult(False, "音高没有变化。")
        self._checkpoint()
        self.score.events[index].midi = midi
        self._normalize()
        return EditResult(True, "音高已更新。")

    def scale_duration(self, index: int, factor: float) -> EditResult:
        if not (0 <= index < len(self.score.events)):
            return EditResult(False, "没有选中有效音符。")
        if factor <= 0:
            return EditResult(False, "时值倍率无效。")
        self._checkpoint()
        e = self.score.events[index]
        e.duration_beats = max(1/128, min(64.0, e.duration_beats * factor))
        return EditResult(True, f"时值调整为 {e.duration_beats:.4g} 拍。")

    def set_duration(self, index: int, beats: float) -> EditResult:
        if not (0 <= index < len(self.score.events)):
            return EditResult(False, "没有选中有效音符。")
        beats = max(1/128, min(64.0, float(beats)))
        if abs(self.score.events[index].duration_beats - beats) < 1e-9:
            return EditResult(False, "时值没有变化。")
        self._checkpoint()
        self.score.events[index].duration_beats = beats
        return EditResult(True, "时值已更新。")

    def delete(self, index: int) -> EditResult:
        if not (0 <= index < len(self.score.events)):
            return EditResult(False, "没有选中有效音符。")
        self._checkpoint()
        event = self.score.events.pop(index)
        return EditResult(True, f"已删除 {event.pitch_name}。")

    def undo(self) -> EditResult:
        if not self._undo:
            return EditResult(False, "没有可以撤销的操作。")
        self._redo.append(deepcopy(self.score.events))
        self.score.events = self._undo.pop()
        self._normalize()
        return EditResult(True, "已撤销。")

    def redo(self) -> EditResult:
        if not self._redo:
            return EditResult(False, "没有可以重做的操作。")
        self._undo.append(deepcopy(self.score.events))
        self.score.events = self._redo.pop()
        self._normalize()
        return EditResult(True, "已重做。")

    def reset_all(self) -> EditResult:
        if self.score.events == self._baseline:
            return EditResult(False, "当前已经是原始识别结果。")
        self._checkpoint()
        self.score.events = deepcopy(self._baseline)
        self._normalize()
        return EditResult(True, "已恢复全部原始识别音符。")
