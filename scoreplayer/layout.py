from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable
import itertools

import cv2
import numpy as np

from scoreplayer.preprocess import detect_staff_lines, estimate_staff_spacing


@dataclass
class SystemGeometry:
    page_index: int
    system_index: int
    x0: int
    y0: int
    x1: int
    y1: int
    boundaries: list[int]
    detected_measure_count: int
    confidence: float = 1.0


@dataclass
class PageGeometry:
    page_index: int
    path: str
    width: int
    height: int
    staff_spacing: float
    systems: list[SystemGeometry] = field(default_factory=list)


@dataclass
class MeasureRegion:
    measure: int
    page_index: int
    system_index: int
    x0: int
    y0: int
    x1: int
    y1: int
    confidence: float
    mode: str = "detected"


@dataclass
class FollowMap:
    pages: list[PageGeometry]
    regions: dict[int, MeasureRegion]
    detected_measure_count: int
    target_measure_count: int
    warnings: list[str] = field(default_factory=list)


def _cluster(values: Iterable[int], gap: int = 2) -> list[float]:
    vals = [int(v) for v in values]
    if not vals:
        return []
    vals.sort()
    groups: list[list[int]] = [[vals[0]]]
    for value in vals[1:]:
        if value - groups[-1][-1] <= gap:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [sum(group) / len(group) for group in groups]


def _cluster_positions(values: Iterable[int], gap: int = 8) -> list[int]:
    vals = [int(v) for v in values]
    if not vals:
        return []
    vals.sort()
    groups: list[list[int]] = [[vals[0]]]
    for value in vals[1:]:
        if value - groups[-1][-1] <= gap:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [int(round(sum(group) / len(group))) for group in groups]


def _long_runs(mask: np.ndarray, minimum_length: int) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start = None
    for i, value in enumerate(mask.tolist()):
        if value and start is None:
            start = i
        if (not value or i == len(mask) - 1) and start is not None:
            end = i if not value else i + 1
            if end - start >= minimum_length:
                runs.append((start, end))
            start = None
    return runs


def _staff_candidate_scores(gray: np.ndarray, spacing: float):
    inverted = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )[1]
    height, width = gray.shape
    step = max(3, int(round(spacing)))

    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(10, width // 60), 1)
    )
    horizontal = cv2.morphologyEx(inverted, cv2.MORPH_OPEN, kernel)
    row_ink = np.count_nonzero(horizontal > 0, axis=1).astype(float)

    scores = np.zeros(max(1, height - 4 * step), dtype=float)
    for y in range(len(scores)):
        scores[y] = np.mean([row_ink[y + k * step] for k in range(5)])

    candidates = [
        (float(scores[y]), y)
        for y in range(1, len(scores) - 1)
        if scores[y] >= scores[y - 1] and scores[y] >= scores[y + 1]
    ]
    candidates.sort(reverse=True)

    selected: list[tuple[float, int]] = []
    for score, y in candidates:
        if all(abs(y - existing_y) > step * 4.0 for _, existing_y in selected):
            selected.append((score, y))
        if len(selected) >= 32:
            break

    max_score = max((score for score, _ in selected), default=1.0)
    selected = [
        (score, y)
        for score, y in selected
        if score >= max_score * 0.34
    ]
    selected.sort(key=lambda item: item[1])
    return selected, inverted, horizontal


def _pair_staff_candidates(
    candidates: list[tuple[float, int]], spacing: float
) -> list[tuple[tuple[float, int], tuple[float, int]]]:
    """
    Pair upper/lower piano staves. A DP is used instead of simply taking
    candidates two-by-two because dynamics, beams and text can create false
    five-line-looking bands.
    """
    n = len(candidates)
    min_gap = spacing * 6.0
    max_gap = spacing * 19.0

    @lru_cache(None)
    def dp(i: int):
        if i >= n:
            return 0.0, ()

        best_score, best_pairs = dp(i + 1)
        score_i, y_i = candidates[i]

        for j in range(i + 1, n):
            score_j, y_j = candidates[j]
            gap = y_j - y_i
            if gap > max_gap:
                break
            if gap < min_gap:
                continue

            preference = max(
                0.65,
                1.0 - abs(gap / spacing - 11.0) * 0.03,
            )
            tail_score, tail_pairs = dp(j + 1)
            score = (score_i + score_j) * preference + tail_score
            if score > best_score:
                best_score = score
                best_pairs = ((i, j),) + tail_pairs

        return best_score, best_pairs

    _, pair_indices = dp(0)
    return [(candidates[i], candidates[j]) for i, j in pair_indices]


def _match_staff_lines(
    line_candidates: list[float],
    approximate_y: float,
    spacing: float,
) -> list[float]:
    nearby = [
        float(y)
        for y in line_candidates
        if approximate_y - 3 * spacing <= y <= approximate_y + 7 * spacing
    ]

    best: tuple[float, tuple[float, ...]] | None = None
    for combination in itertools.combinations(nearby, 5):
        gaps = np.diff(np.array(combination, dtype=float))
        if np.all(
            (gaps >= spacing * 0.65)
            & (gaps <= spacing * 1.45)
        ):
            error = float(np.mean(np.abs(gaps - spacing)))
            error += abs(combination[0] - approximate_y) * 0.05
            if best is None or error < best[0]:
                best = (error, combination)

    if best is not None:
        return list(best[1])

    return [approximate_y + k * spacing for k in range(5)]


def _system_x_ranges(
    horizontal: np.ndarray,
    upper_lines: list[float],
    lower_lines: list[float],
) -> list[tuple[int, int]]:
    height, width = horizontal.shape
    top = max(0, int(round(upper_lines[0] - 2)))
    bottom = min(height, int(round(lower_lines[-1] + 3)))
    band = horizontal[top:bottom]

    column_ink = np.count_nonzero(band > 0, axis=0).astype(float)
    smoothed = np.convolve(column_ink, np.ones(9) / 9, mode="same")
    mask = smoothed >= 1.2

    one_row = (mask.astype(np.uint8) * 255)[None, :]
    close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 1))
    one_row = cv2.morphologyEx(one_row, cv2.MORPH_CLOSE, close_kernel)

    # A page may contain two separate systems on the same physical row
    # (for example Coda material). Keep every sufficiently long run.
    runs = _long_runs(
        one_row[0] > 0,
        max(140, int(width * 0.18)),
    )
    if not runs:
        return [(int(width * 0.05), int(width * 0.95))]
    return runs


def _line_continuity(
    inverted: np.ndarray,
    top: float,
    bottom: float,
    x: int,
) -> tuple[float, int]:
    height, width = inverted.shape
    y0 = max(0, int(round(top)))
    y1 = min(height - 1, int(round(bottom)))
    band = inverted[y0:y1 + 1, max(0, x - 1):min(width, x + 2)] > 0
    if band.size == 0:
        return 0.0, 999

    present = np.any(band, axis=1)
    fraction = float(present.mean())

    longest_gap = 0
    current_gap = 0
    for value in present.tolist():
        if not value:
            current_gap += 1
            longest_gap = max(longest_gap, current_gap)
        else:
            current_gap = 0
    return fraction, longest_gap


def _detect_barlines(
    inverted: np.ndarray,
    top: float,
    bottom: float,
    x0: int,
    x1: int,
) -> tuple[list[int], float]:
    y0 = int(round(top))
    y1 = int(round(bottom))
    band = inverted[y0:y1 + 1, x0:x1]
    height = band.shape[0]
    if height < 20 or band.shape[1] < 50:
        return [x0, x1], 0.2

    vertical_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (1, max(25, int(height * 0.72))),
    )
    vertical = cv2.morphologyEx(band, cv2.MORPH_OPEN, vertical_kernel)
    column_ink = np.count_nonzero(vertical > 0, axis=0)

    candidate_x = np.where(column_ink >= height * 0.55)[0]
    raw = [
        x0 + int(round(x))
        for x in _cluster(candidate_x, gap=3)
    ]

    accepted: list[int] = []
    qualities: list[float] = []
    for x in raw:
        fraction, longest_gap = _line_continuity(
            inverted, top, bottom, x
        )
        # The second, softer branch handles systems whose line detector is
        # shifted a few pixels by dense chords/ledger lines.
        if fraction >= 0.96 and longest_gap <= 3:
            accepted.append(x)
            qualities.append(1.0)
        elif fraction >= 0.90 and longest_gap <= 12:
            accepted.append(x)
            qualities.append(0.72)

    accepted = _cluster_positions(accepted, gap=8)

    if not accepted or abs(accepted[0] - x0) > 20:
        accepted.insert(0, x0 + 3)
        qualities.append(0.65)
    if abs(accepted[-1] - x1) > 20:
        accepted.append(x1 - 5)
        qualities.append(0.65)

    accepted = sorted(set(accepted))
    if len(accepted) < 2:
        accepted = [x0 + 3, x1 - 5]

    confidence = float(np.mean(qualities)) if qualities else 0.55
    return accepted, confidence


def analyze_page(path: str | Path, page_index: int = 0) -> PageGeometry:
    path = Path(path)
    gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError(f"无法读取曲谱图片：{path}")

    height, width = gray.shape
    spacing = estimate_staff_spacing(gray) or max(6.0, width / 150.0)
    line_candidates = detect_staff_lines(gray)
    staff_candidates, inverted, horizontal = _staff_candidate_scores(
        gray, spacing
    )
    staff_pairs = _pair_staff_candidates(staff_candidates, spacing)

    systems: list[SystemGeometry] = []
    pending: list[tuple[float, int, int, list[float], list[float]]] = []

    for upper_candidate, lower_candidate in staff_pairs:
        upper_lines = _match_staff_lines(
            line_candidates, upper_candidate[1], spacing
        )
        lower_lines = _match_staff_lines(
            line_candidates, lower_candidate[1], spacing
        )

        ranges = _system_x_ranges(
            horizontal, upper_lines, lower_lines
        )
        for x0, x1 in ranges:
            pending.append((
                upper_lines[0],
                x0,
                x1,
                upper_lines,
                lower_lines,
            ))

    # Reading order: top-to-bottom, then left-to-right for split systems.
    pending.sort(key=lambda item: (item[0], item[1]))

    for system_index, (_sort_y, x0, x1, upper_lines, lower_lines) in enumerate(pending):
        boundaries, confidence = _detect_barlines(
            inverted,
            upper_lines[0],
            lower_lines[-1],
            x0,
            x1,
        )
        y_margin = max(10, int(round(spacing * 2.0)))
        systems.append(SystemGeometry(
            page_index=page_index,
            system_index=system_index,
            x0=int(x0),
            y0=max(0, int(round(upper_lines[0])) - y_margin),
            x1=int(x1),
            y1=min(height - 1, int(round(lower_lines[-1])) + y_margin),
            boundaries=boundaries,
            detected_measure_count=max(1, len(boundaries) - 1),
            confidence=confidence,
        ))

    return PageGeometry(
        page_index=page_index,
        path=str(path),
        width=width,
        height=height,
        staff_spacing=float(spacing),
        systems=systems,
    )


def _reconciled_counts(
    systems: list[SystemGeometry],
    target_measure_count: int,
) -> list[int]:
    counts = [max(1, s.detected_measure_count) for s in systems]
    if not systems or target_measure_count <= 0:
        return counts

    target_measure_count = max(len(systems), int(target_measure_count))

    while sum(counts) < target_measure_count:
        candidates = [
            (
                (systems[i].x1 - systems[i].x0) / max(1, counts[i])
                * (1.0 + (1.0 - systems[i].confidence) * 0.35),
                i,
            )
            for i in range(len(systems))
            if counts[i] < 8
        ]
        if not candidates:
            break
        _, index = max(candidates)
        counts[index] += 1

    while sum(counts) > target_measure_count:
        candidates = [
            (
                systems[i].confidence
                + min(1.0, (systems[i].x1 - systems[i].x0) / 1000.0) * 0.15,
                i,
            )
            for i in range(len(systems))
            if counts[i] > 1
        ]
        if not candidates:
            break
        _, index = min(candidates)
        counts[index] -= 1

    return counts


def _boundaries_for_count(system: SystemGeometry, count: int) -> tuple[list[int], str]:
    raw_count = max(1, len(system.boundaries) - 1)
    if raw_count == count and len(system.boundaries) >= 2:
        return system.boundaries, "detected"

    # If OMR measure count and image barline count disagree, preserve the
    # correct page/system while subdividing its horizontal span evenly.
    width = max(1, system.x1 - system.x0)
    boundaries = [
        int(round(system.x0 + width * i / count))
        for i in range(count + 1)
    ]
    return boundaries, "reconciled"


def build_follow_map(
    image_paths: list[str | Path],
    target_measure_count: int,
) -> FollowMap:
    pages = [
        analyze_page(path, page_index=i)
        for i, path in enumerate(image_paths)
    ]
    systems = [
        system
        for page in pages
        for system in page.systems
    ]
    systems.sort(key=lambda s: (s.page_index, s.y0, s.x0))

    raw_total = sum(s.detected_measure_count for s in systems)
    warnings: list[str] = []

    if not systems:
        return FollowMap(
            pages=pages,
            regions={},
            detected_measure_count=0,
            target_measure_count=target_measure_count,
            warnings=["没有从曲谱图片中检测到可跟随的钢琴系统。"],
        )

    counts = _reconciled_counts(systems, target_measure_count)
    if target_measure_count > 0 and raw_total != target_measure_count:
        warnings.append(
            f"图像检测到约 {raw_total} 个小节，而 MusicXML 有 "
            f"{target_measure_count} 个；已在保持页面/系统顺序的前提下自动对齐。"
        )

    regions: dict[int, MeasureRegion] = {}
    measure_number = 1

    for system, count in zip(systems, counts):
        boundaries, mode = _boundaries_for_count(system, count)
        for i in range(count):
            if target_measure_count > 0 and measure_number > target_measure_count:
                break
            x0 = min(boundaries[i], boundaries[i + 1])
            x1 = max(boundaries[i], boundaries[i + 1])
            pad = max(4, int((x1 - x0) * 0.025))
            regions[measure_number] = MeasureRegion(
                measure=measure_number,
                page_index=system.page_index,
                system_index=system.system_index,
                x0=max(system.x0, x0 - pad),
                y0=system.y0,
                x1=min(system.x1, x1 + pad),
                y1=system.y1,
                confidence=system.confidence,
                mode=mode,
            )
            measure_number += 1

    return FollowMap(
        pages=pages,
        regions=regions,
        detected_measure_count=raw_total,
        target_measure_count=target_measure_count,
        warnings=warnings,
    )
