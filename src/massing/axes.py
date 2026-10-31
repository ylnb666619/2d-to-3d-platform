"""Axis grid labels: the drawing's own alignment datum.

Plans are drawn side by side on a sheet and have to be moved onto one origin
before they can be stacked. Aligning by the bounding-box centre fails whenever
two plans have different extents — the ground floor usually carries steps, a
ramp or a canopy that a typical floor does not, which shifts its centre by
metres and lands the whole storey in the wrong place.

Axis bubbles are the same physical grid on every plan, so the same label
("3", "B") on two plans gives an exact offset. ZWCAD Architecture stores them
as ``SWR_AXIS_LABEL`` objects whose proxy graphic holds a circle and its text.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class AxisLabel:
    text: str
    x: float
    y: float


def _children(entity: Any) -> list[Any]:
    raw = getattr(entity, "proxy_graphic", None)
    if not raw:
        return []
    try:
        from ezdxf.proxygraphic import ProxyGraphic

        return list(ProxyGraphic(raw).virtual_entities())
    except Exception:  # noqa: BLE001
        return []


def _normalise(value: str) -> str:
    """``"11"`` -> ``"1"``.

    The proxy graphic draws each bubble's text twice (the label and its
    mirrored copy), so a single-label object's text reads as the label
    repeated.
    """
    text = (value or "").strip().replace(" ", "")
    if len(text) >= 2 and len(text) % 2 == 0:
        half = len(text) // 2
        if text[:half] == text[half:]:
            return text[:half]
    return text


def read_axis_labels(document: Any) -> list[AxisLabel]:
    """Every axis bubble in model space: its label and its centre."""
    labels: list[AxisLabel] = []
    for entity in document.modelspace():
        if entity.dxftype() != "SWR_AXIS_LABEL":
            continue
        circles: list[tuple[float, float]] = []
        texts: list[tuple[str, float, float]] = []
        for child in _children(entity):
            kind = child.dxftype()
            try:
                if kind == "CIRCLE":
                    centre = child.dxf.center
                    circles.append((float(centre.x), float(centre.y)))
                elif kind in {"TEXT", "MTEXT"}:
                    value = child.plain_text() if kind == "MTEXT" else child.dxf.text
                    point = child.dxf.insert
                    texts.append((str(value), float(point.x), float(point.y)))
            except Exception:  # noqa: BLE001
                continue
        for value, x, y in texts:
            text = _normalise(value)
            if not text:
                continue
            # The bubble, not the glyph, sits on the axis line.
            if circles:
                cx, cy = min(circles, key=lambda point: math.dist((x, y), point))
                if math.dist((x, y), (cx, cy)) <= 3000.0:
                    labels.append(AxisLabel(text=text, x=cx, y=cy))
                    continue
            labels.append(AxisLabel(text=text, x=x, y=y))
    return labels


def labels_in(
    labels: Iterable[AxisLabel],
    bounds: tuple[float, float, float, float],
    *,
    pad: float = 2000.0,
) -> list[AxisLabel]:
    x0, y0, x1, y1 = bounds
    return [
        label
        for label in labels
        if x0 - pad <= label.x <= x1 + pad and y0 - pad <= label.y <= y1 + pad
    ]


def align_offset(
    reference: list[AxisLabel],
    target: list[AxisLabel],
    *,
    tolerance: float = 10.0,
    limit: float = 300_000.0,
) -> tuple[float, float] | None:
    """The ``(dx, dy)`` that moves ``target`` onto ``reference``.

    Labels unique on both plans give an unambiguous correspondence and are
    preferred; when a plan repeats a label (bubbles at both ends of an axis)
    the answer is the most common offset, which the repeated pairs agree on.
    A single coincidence is not evidence, so one match is only accepted when
    that label occurs exactly once on each plan.
    """
    if not reference or not target:
        return None

    def counts(labels: list[AxisLabel]) -> Counter:
        return Counter(label.text for label in labels)

    reference_counts, target_counts = counts(reference), counts(target)
    votes: Counter = Counter()
    unique_votes: Counter = Counter()
    for label in target:
        for other in reference:
            if other.text != label.text:
                continue
            dx = other.x - label.x
            dy = other.y - label.y
            if abs(dx) > limit or abs(dy) > limit:
                continue
            key = (round(dx / tolerance) * tolerance, round(dy / tolerance) * tolerance)
            votes[key] += 1
            if reference_counts[label.text] == 1 and target_counts[label.text] == 1:
                unique_votes[key] += 1

    for tally, minimum in ((unique_votes, 1), (votes, 2)):
        if not tally:
            continue
        (dx, dy), support = tally.most_common(1)[0]
        if support >= minimum:
            return float(dx), float(dy)
    return None
