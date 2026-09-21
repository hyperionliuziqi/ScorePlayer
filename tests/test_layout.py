from pathlib import Path

import cv2
import numpy as np

from scoreplayer.layout import analyze_page, build_follow_map


def _make_page(path: Path):
    image = np.full((900, 1200, 3), 255, dtype=np.uint8)

    for system_y in (140, 500):
        upper = system_y
        lower = system_y + 92

        for staff_y in (upper, lower):
            for line in range(5):
                y = staff_y + line * 8
                cv2.line(image, (100, y), (1100, y), (0, 0, 0), 2)

        # 3 measures => four full grand-staff barlines.
        for x in (100, 430, 760, 1100):
            cv2.line(
                image,
                (x, upper),
                (x, lower + 32),
                (0, 0, 0),
                2,
            )

        # A few noteheads/stems: should not be mistaken for system barlines.
        for x, offset in ((220, 12), (310, 20), (570, 4), (890, 26)):
            y = upper + offset
            cv2.ellipse(image, (x, y), (8, 5), -15, 0, 360, (0, 0, 0), -1)
            cv2.line(image, (x + 7, y), (x + 7, y - 38), (0, 0, 0), 2)

    cv2.imwrite(str(path), image)


def test_detects_two_systems_and_six_measures(tmp_path):
    image = tmp_path / "page.png"
    _make_page(image)

    page = analyze_page(image)
    assert len(page.systems) == 2
    assert sum(s.detected_measure_count for s in page.systems) == 6


def test_follow_map_assigns_sequential_measures(tmp_path):
    image = tmp_path / "page.png"
    _make_page(image)

    follow = build_follow_map([image], target_measure_count=6)
    assert set(follow.regions) == {1, 2, 3, 4, 5, 6}
    assert follow.regions[1].page_index == 0
    assert follow.regions[6].system_index == 1
