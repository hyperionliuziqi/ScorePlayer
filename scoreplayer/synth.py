from __future__ import annotations
from pathlib import Path
import wave
import numpy as np

from scoreplayer.model import Score
from scoreplayer.musicxml import event_times_seconds


def midi_frequency(midi: int) -> float:
    return 440.0 * (2.0 ** ((midi - 69) / 12.0))


def _piano_like_tone(freq: float, duration: float, sr: int) -> np.ndarray:
    n = max(1, int(duration * sr))
    t = np.arange(n, dtype=np.float64) / sr

    # Additive approximation. It is intentionally self-contained so the app can play
    # without any external soundfont or audio software.
    harmonics = (
        1.00 * np.sin(2 * np.pi * freq * t) +
        0.38 * np.sin(2 * np.pi * freq * 2.00 * t) +
        0.18 * np.sin(2 * np.pi * freq * 3.01 * t) +
        0.09 * np.sin(2 * np.pi * freq * 4.02 * t)
    )

    attack = min(0.012, duration * 0.2)
    decay = 2.8 + freq / 1800.0
    envelope = np.exp(-decay * t)

    if attack > 0:
        attack_n = min(n, max(1, int(attack * sr)))
        envelope[:attack_n] *= np.linspace(0, 1, attack_n)

    # tiny release avoids clicks
    release_n = min(n, max(1, int(min(0.08, duration * 0.2) * sr)))
    envelope[-release_n:] *= np.linspace(1, 0, release_n)

    return harmonics * envelope


def render_wav(
    score: Score,
    output: str | Path,
    bpm_override: float | None = None,
    sample_rate: int = 44100,
) -> Path:
    output = Path(output)
    timed = event_times_seconds(score, bpm_override)
    if not timed:
        raise ValueError("没有可合成的音符。")

    total_seconds = max(start + dur for _, start, dur in timed) + 0.35
    mix = np.zeros(int(total_seconds * sample_rate) + 1, dtype=np.float64)

    for event, start, duration in timed:
        # Give very short recognized notes enough audible body but preserve onset.
        dur = max(0.06, min(duration * 0.95, 8.0))
        tone = _piano_like_tone(midi_frequency(event.midi), dur, sample_rate)
        start_i = int(start * sample_rate)
        end_i = min(len(mix), start_i + len(tone))
        mix[start_i:end_i] += tone[:end_i - start_i] * (event.velocity / 127.0)

    peak = float(np.max(np.abs(mix))) or 1.0
    mix = np.clip(mix / peak * 0.90, -1.0, 1.0)
    pcm = (mix * 32767.0).astype("<i2")

    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())

    return output
