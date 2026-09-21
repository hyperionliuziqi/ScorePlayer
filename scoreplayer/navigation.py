from __future__ import annotations
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass
class MeasureNav:
    index: int
    number: str
    repeat_forward: bool = False
    repeat_backward: bool = False
    repeat_times: int = 2
    ending_numbers: set[int] | None = None
    ending_stop: bool = False
    segno: bool = False
    coda: bool = False
    dc: bool = False
    ds: bool = False
    to_coda: bool = False
    fine: bool = False


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _words(measure: ET.Element) -> str:
    out = []
    for node in measure.iter():
        if _local(node.tag) == "words" and node.text:
            out.append(node.text.strip())
    return " ".join(out).lower()


def _parse_ending_numbers(value: str | None) -> set[int] | None:
    if not value:
        return None
    nums = {int(x) for x in re.findall(r"\d+", value)}
    return nums or None


def parse_navigation(measures: list[ET.Element]) -> list[MeasureNav]:
    result: list[MeasureNav] = []
    for idx, measure in enumerate(measures):
        nav = MeasureNav(index=idx, number=measure.attrib.get("number", str(idx + 1)))
        words = _words(measure)

        for node in measure.iter():
            tag = _local(node.tag)

            if tag == "repeat":
                direction = node.attrib.get("direction", "")
                if direction == "forward":
                    nav.repeat_forward = True
                elif direction == "backward":
                    nav.repeat_backward = True
                    try:
                        nav.repeat_times = max(2, int(node.attrib.get("times", "2")))
                    except ValueError:
                        nav.repeat_times = 2

            elif tag == "ending":
                typ = node.attrib.get("type", "")
                if typ == "start":
                    nav.ending_numbers = _parse_ending_numbers(node.attrib.get("number"))
                elif typ in {"stop", "discontinue"}:
                    nav.ending_stop = True

            elif tag == "segno":
                nav.segno = True
            elif tag == "coda":
                nav.coda = True
            elif tag == "sound":
                if node.attrib.get("dacapo") == "yes":
                    nav.dc = True
                if "dalsegno" in node.attrib:
                    nav.ds = True
                if "tocoda" in node.attrib:
                    nav.to_coda = True
                if node.attrib.get("fine") == "yes":
                    nav.fine = True

        if "d.c." in words or "da capo" in words:
            nav.dc = True
        if "d.s." in words or "dal segno" in words:
            nav.ds = True
        if "to coda" in words:
            nav.to_coda = True
        if re.search(r"\bfine\b", words):
            nav.fine = True

        result.append(nav)
    return result


def expand_measure_order(measures: list[ET.Element], max_factor: int = 8) -> tuple[list[int], list[str]]:
    """
    Expand common playback navigation:
    - forward/backward repeats
    - simple 1st/2nd endings
    - D.C. / D.S.
    - Fine after a D.C./D.S. jump
    - To Coda after a D.C./D.S. jump

    This intentionally has a hard step cap so malformed recognition can never loop forever.
    """
    navs = parse_navigation(measures)
    n = len(navs)
    if not n:
        return [], []

    warnings: list[str] = []
    segno_idx = next((x.index for x in navs if x.segno), 0)
    coda_idx = next((x.index for x in navs if x.coda), None)

    order: list[int] = []
    i = 0
    repeat_start = 0
    repeat_visits: dict[int, int] = {}
    jumped = False
    did_dc_ds = False
    active_pass = 1

    max_steps = max(n * max_factor, 32)
    steps = 0

    while 0 <= i < n and steps < max_steps:
        steps += 1
        nav = navs[i]

        # Skip endings that do not belong to this pass.
        if nav.ending_numbers and active_pass not in nav.ending_numbers:
            i += 1
            continue

        order.append(i)

        if jumped and nav.fine:
            break

        if nav.repeat_forward:
            repeat_start = i
            active_pass = 1

        if jumped and nav.to_coda:
            if coda_idx is not None:
                i = coda_idx
                continue
            warnings.append("检测到 To Coda，但没有找到 Coda 目标。")

        if not did_dc_ds and nav.ds:
            did_dc_ds = True
            jumped = True
            i = segno_idx
            continue

        if not did_dc_ds and nav.dc:
            did_dc_ds = True
            jumped = True
            i = 0
            continue

        if nav.repeat_backward:
            count = repeat_visits.get(i, 1)
            if count < nav.repeat_times:
                repeat_visits[i] = count + 1
                active_pass = count + 1
                i = repeat_start
                continue
            active_pass = 1

        i += 1

    if steps >= max_steps:
        warnings.append("演奏路线达到安全上限，已停止展开，防止反复记号造成死循环。")

    return order, warnings
