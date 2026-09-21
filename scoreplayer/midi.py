from __future__ import annotations
from pathlib import Path
import struct

from scoreplayer.model import Score


def _vlq(value: int) -> bytes:
    value = max(0, int(value))
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(out))


def export_midi(score: Score, output: str | Path, ppq: int = 480, bpm: float = 120.0) -> Path:
    output = Path(output)
    events = []

    tempo_us = int(60_000_000 / max(1.0, bpm))
    events.append((0, 0, b"\xFF\x51\x03" + tempo_us.to_bytes(3, "big")))

    for e in score.events:
        start = int(round(e.beat * ppq))
        end = int(round((e.beat + e.duration_beats) * ppq))
        events.append((start, 2, bytes([0x90, max(0, min(127, e.midi)), max(1, min(127, e.velocity))])))
        events.append((end, 1, bytes([0x80, max(0, min(127, e.midi)), 0])))

    events.sort(key=lambda x: (x[0], x[1]))
    track = bytearray()
    last_tick = 0

    for tick, _prio, payload in events:
        track.extend(_vlq(tick - last_tick))
        track.extend(payload)
        last_tick = tick

    track.extend(b"\x00\xFF\x2F\x00")

    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, ppq)
    body = b"MTrk" + struct.pack(">I", len(track)) + bytes(track)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(header + body)
    return output
