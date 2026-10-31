"""Door and window openings, read from the drawing's own objects.

ZWCAD Architecture stores each door and window as an ``SWR_OPENING`` whose
proxy graphic carries the plan symbol and whose text is the schedule tag —
``C1516`` is a 1500 x 1600 window, ``M1021`` a 1000 x 2100 door, ``FM乙1824``
a 1800 x 2400 fire door. The tag is the authority on size; the symbol gives the
position and the direction of the wall it sits in.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

_TAG = re.compile(r"(FM|GC|MD|MLC|TLM|TC|MC|BYC|C|M)\s*[乙甲丙]?\s*(\d{2})\s*(\d{2})")

_KINDS = {
    "FM": "fire_door",
    "MD": "door",
    "MLC": "door",
    "TLM": "door",
    "M": "door",
    "GC": "high_window",
    "TC": "window",
    "MC": "window",
    "BYC": "window",
    "C": "window",
}

# Sill height by opening kind, in millimetres. A door reaches the floor; a
# window starts at the usual 900 mm sill; a high window (GC) sits up at 2400.
SILL_BY_KIND = {
    "door": 0.0,
    "fire_door": 0.0,
    "window": 900.0,
    "high_window": 2400.0,
}


@dataclass
class Opening:
    tag: str
    kind: str
    width: float
    height: float
    center: tuple[float, float]
    angle: float  # radians, direction of the host wall
    sill: float
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = field(default_factory=list)
    # True when the size came from the schedule by width rather than from a
    # tag: the drawing did not label this opening.
    inferred: bool = False

    @property
    def top(self) -> float:
        return self.sill + self.height

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "kind": self.kind,
            "width": round(self.width, 1),
            "height": round(self.height, 1),
            "sill": self.sill,
            "center": [round(self.center[0], 1), round(self.center[1], 1)],
            "angle": round(math.degrees(self.angle), 2),
            "inferred": self.inferred,
        }


def _segment_on_wall(opening: Opening, wall: Any) -> tuple[float, tuple[float, float], float] | None:
    """The opening's own line that lies along ``wall``: ``(distance, midpoint, angle)``.

    A door symbol contains the leaf and its swing arc as well as the opening
    line. The opening line is the one parallel to the wall and on it; taking the
    longest segment instead would place the cut at the leaf's midpoint, up to a
    metre off the wall.
    """
    ax, ay = wall.start
    bx, by = wall.end
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    if length <= 0:
        return None
    ux, uy = dx / length, dy / length

    best = None
    for start, end in opening.segments:
        vx, vy = end[0] - start[0], end[1] - start[1]
        span = math.hypot(vx, vy)
        if span < 50:
            continue
        if abs(ux * vy - uy * vx) / span > 0.15:  # more than about 9 degrees off
            continue
        mx, my = (start[0] + end[0]) / 2, (start[1] + end[1]) / 2
        # Two separate tolerances. Perpendicular: the opening must lie on this
        # wall's line, so a parallel wall in the next room never matches.
        # Along: a wall is drawn split at its openings, so the opening's centre
        # sits in the gap between two pieces — within half its own width of an
        # end, not on the piece.
        perpendicular = abs((mx - ax) * -uy + (my - ay) * ux)
        if perpendicular > 300.0:
            continue
        along = (mx - ax) * ux + (my - ay) * uy
        if along < 0:
            gap = -along
        elif along > length:
            gap = along - length
        else:
            gap = 0.0
        if gap > opening.width / 2 + 300.0:
            continue
        score = perpendicular + gap
        if best is None or score < best[0]:
            best = (score, (mx, my), math.atan2(uy, ux))
    return best


def place_openings(
    openings: list[Opening],
    walls: list[Any],
    *,
    limit: float = 400.0,
) -> list[tuple[Opening, Any, tuple[float, float], float]]:
    """Pair each opening with the wall it cuts: ``(opening, wall, centre, angle)``.

    An opening whose symbol never lies along a wall is left out: cutting it
    somewhere guessed would be worse than leaving it uncut.
    """
    placed = []
    for opening in openings:
        best = None
        for wall in walls:
            match = _segment_on_wall(opening, wall)
            if match is None:
                continue
            if best is None or match[0] < best[0]:
                best = (match[0], wall, match[1], match[2])
        if best is not None:
            placed.append((opening, best[1], best[2], best[3]))
    return placed


def _children(entity: Any) -> list[Any]:
    raw = getattr(entity, "proxy_graphic", None)
    if not raw:
        return []
    try:
        from ezdxf.proxygraphic import ProxyGraphic

        return list(ProxyGraphic(raw).virtual_entities())
    except Exception:  # noqa: BLE001
        return []


def _segments(children: list[Any]) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    segments = []
    for child in children:
        kind = child.dxftype()
        try:
            if kind == "LINE":
                segments.append(
                    ((float(child.dxf.start.x), float(child.dxf.start.y)),
                     (float(child.dxf.end.x), float(child.dxf.end.y)))
                )
            elif kind in {"LWPOLYLINE", "POLYLINE"}:
                points = (
                    [(float(x), float(y)) for x, y, _b in child.get_points("xyb")]
                    if kind == "LWPOLYLINE"
                    else [(float(v.x), float(v.y)) for v in child.points()]
                )
                segments.extend(zip(points, points[1:]))
                if kind == "LWPOLYLINE" and child.closed and len(points) > 2:
                    segments.append((points[-1], points[0]))
        except Exception:  # noqa: BLE001
            continue
    return [segment for segment in segments if segment[0] != segment[1]]


def _text_of(children: list[Any]) -> str:
    parts = []
    for child in children:
        if child.dxftype() in {"TEXT", "MTEXT"}:
            try:
                value = child.plain_text() if child.dxftype() == "MTEXT" else child.dxf.text
                parts.append(str(value).strip())
            except Exception:  # noqa: BLE001
                pass
    return "".join(parts).replace(" ", "")


def parse_tag(tag: str) -> tuple[str, float, float] | None:
    """``C1516`` -> ("window", 1500, 1600)."""
    match = _TAG.search(tag)
    if not match:
        return None
    prefix, width, height = match.groups()
    kind = _KINDS[prefix]
    return kind, float(int(width) * 100), float(int(height) * 100)


MIN_INFERRED_WIDTH = 300.0


def read_openings(document: Any, schedule: Any | None = None) -> list[Opening]:
    """Every ``SWR_OPENING`` that has a usable size.

    A labelled opening takes its size from its tag. An unlabelled one — this
    drawing has 42 of them, and they are real holes in real walls — takes its
    width from the symbol's own width line and its height from the schedule,
    and is marked ``inferred`` so the report can say where it came from.
    """
    openings: list[Opening] = []
    for entity in document.modelspace():
        if entity.dxftype() != "SWR_OPENING":
            continue
        children = _children(entity)
        if not children:
            continue
        segments = _segments(children)
        if not segments:
            continue
        # The longest line is the opening's own width line across the wall; the
        # rest of the symbol (leaf, swing arc, glazing) is detail. Most window
        # symbols do not close into a polygon, so the line is the reliable part.
        longest = max(segments, key=lambda segment: math.dist(segment[0], segment[1]))
        center = ((longest[0][0] + longest[1][0]) / 2, (longest[0][1] + longest[1][1]) / 2)
        angle = math.atan2(longest[1][1] - longest[0][1], longest[1][0] - longest[0][0])

        text = _text_of(children)
        parsed = parse_tag(text) if text else None
        if parsed is not None:
            kind, width, height = parsed
            openings.append(
                Opening(
                    tag=text,
                    kind=kind,
                    width=width,
                    height=height,
                    center=center,
                    angle=angle,
                    sill=SILL_BY_KIND.get(kind, 0.0),
                    segments=segments,
                )
            )
            continue

        if schedule is None:
            continue
        # A door symbol's longest line is the leaf's diagonal, not the opening:
        # an 800 door measures 1089 across its swing. So rather than trusting
        # the longest line, try every line and keep the widest one that matches
        # a scheduled width.
        choice = None
        for start, end in segments:
            span = math.dist(start, end)
            if span < MIN_INFERRED_WIDTH:
                continue
            row = schedule.match_width(span)
            if row is None:
                continue
            if choice is None or span > choice[0]:
                choice = (span, row, start, end)
        if choice is None:
            continue
        _span, row, start, end = choice
        openings.append(
            Opening(
                tag="",
                kind=row.kind,
                width=row.width,
                height=row.height,
                center=((start[0] + end[0]) / 2, (start[1] + end[1]) / 2),
                angle=math.atan2(end[1] - start[1], end[0] - start[0]),
                sill=SILL_BY_KIND.get(row.kind, 0.0),
                segments=segments,
                inferred=True,
            )
        )
    return openings


def openings_in(
    openings: list[Opening],
    bounds: tuple[float, float, float, float],
    *,
    pad: float = 500.0,
) -> list[Opening]:
    x0, y0, x1, y1 = bounds
    return [
        opening
        for opening in openings
        if x0 - pad <= opening.center[0] <= x1 + pad and y0 - pad <= opening.center[1] <= y1 + pad
    ]
