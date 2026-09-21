from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import math

import cv2
import numpy as np


@dataclass
class PreprocessInfo:
    source: str
    output: str
    crop_box: tuple[int, int, int, int]
    skew_degrees: float
    staff_spacing_px: float | None
    scale: float


def _cluster_rows(rows: np.ndarray, gap: int = 2) -> list[float]:
    if len(rows) == 0:
        return []
    groups: list[list[int]] = [[int(rows[0])]]
    for y in rows[1:]:
        y = int(y)
        if y - groups[-1][-1] <= gap:
            groups[-1].append(y)
        else:
            groups.append([y])
    return [sum(g) / len(g) for g in groups]


def detect_staff_lines(gray: np.ndarray) -> list[float]:
    inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    h, w = inv.shape
    if w < 20:
        return []

    # Long horizontal opening: preserves staff lines while suppressing notes/text.
    kernel_len = max(20, w // 25)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_len, 1))
    horizontal = cv2.morphologyEx(inv, cv2.MORPH_OPEN, kernel)

    row_ink = np.count_nonzero(horizontal > 0, axis=1)
    threshold = max(int(w * 0.20), int(np.percentile(row_ink, 92) * 0.70))
    rows = np.where(row_ink >= threshold)[0]
    return _cluster_rows(rows, gap=2)


def estimate_staff_spacing(gray: np.ndarray) -> float | None:
    lines = detect_staff_lines(gray)
    if len(lines) < 5:
        return None

    diffs = np.diff(np.array(lines))
    diffs = diffs[(diffs >= 3) & (diffs <= 40)]
    if len(diffs) < 4:
        return None

    # Staff-line spacing appears repeatedly. Use the densest integer bin.
    bins = np.rint(diffs).astype(int)
    values, counts = np.unique(bins, return_counts=True)
    mode = float(values[int(np.argmax(counts))])

    close = diffs[np.abs(diffs - mode) <= max(1.5, mode * 0.20)]
    return float(np.median(close)) if len(close) else mode


def estimate_skew(gray: np.ndarray) -> float:
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    min_len = max(80, gray.shape[1] // 4)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 1800, threshold=100,
        minLineLength=min_len, maxLineGap=20
    )
    if lines is None:
        return 0.0

    angles = []
    for line in lines[:, 0]:
        x1, y1, x2, y2 = map(float, line)
        dx = x2 - x1
        dy = y2 - y1
        if abs(dx) < 1:
            continue
        angle = math.degrees(math.atan2(dy, dx))
        if -5.0 <= angle <= 5.0:
            angles.append(angle)

    if not angles:
        return 0.0
    return float(np.median(np.array(angles)))


def rotate_keep_bounds(image: np.ndarray, angle_deg: float) -> np.ndarray:
    if abs(angle_deg) < 0.05:
        return image
    h, w = image.shape[:2]
    center = (w / 2, h / 2)
    matrix = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
    cos = abs(matrix[0, 0])
    sin = abs(matrix[0, 1])
    new_w = int(h * sin + w * cos)
    new_h = int(h * cos + w * sin)
    matrix[0, 2] += new_w / 2 - center[0]
    matrix[1, 2] += new_h / 2 - center[1]
    return cv2.warpAffine(image, matrix, (new_w, new_h), borderValue=(255, 255, 255))


def content_crop(image: np.ndarray, margin_ratio: float = 0.025) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask = gray < 235
    ys, xs = np.where(mask)

    if len(xs) < 100:
        h, w = gray.shape
        return image, (0, 0, w, h)

    h, w = gray.shape
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    mx = int(w * margin_ratio)
    my = int(h * margin_ratio)

    x0 = max(0, x0 - mx)
    y0 = max(0, y0 - my)
    x1 = min(w, x1 + mx)
    y1 = min(h, y1 + my)
    return image[y0:y1, x0:x1], (x0, y0, x1, y1)


def preprocess_page(
    source: str | Path,
    output: str | Path,
    target_staff_spacing: float = 12.0,
) -> PreprocessInfo:
    source = Path(source)
    output = Path(output)
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"无法读取图片：{source}")

    # Initial crop reduces title/large blank margins without deleting score ink.
    image, crop_box = content_crop(image)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    skew = estimate_skew(gray)
    if abs(skew) <= 3.0:
        image = rotate_keep_bounds(image, skew)
    else:
        # Large Hough angles are usually false positives from stems/braces.
        skew = 0.0

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    spacing = estimate_staff_spacing(gray)

    scale = 1.0
    if spacing and 4 <= spacing <= 30:
        scale = float(np.clip(target_staff_spacing / spacing, 0.75, 2.5))
        if abs(scale - 1.0) > 0.03:
            image = cv2.resize(
                image, None, fx=scale, fy=scale,
                interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
            )

    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), image):
        raise IOError(f"无法写入预处理图片：{output}")

    return PreprocessInfo(
        source=str(source),
        output=str(output),
        crop_box=crop_box,
        skew_degrees=skew,
        staff_spacing_px=spacing,
        scale=scale,
    )
