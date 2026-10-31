"""Stair wells, read from the drawing's own stair objects.

ZWCAD Architecture stores a stair as an ``SWR_RECTSTAIR`` custom object whose
proxy graphic draws the flights, the treads and the 上/下 direction label. The
outline of that graphic is the stair well — exactly the hole a floor slab has
to have over it.

Reading the object beats naming the stairs from the 楼梯间 room label: the
label gives a point, and a point has no size.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

from shapely.geometry import Polygon, box as shapely_box


@dataclass
class StairWell:
    bounds: tuple[float, float, float, float]
    label: str = ""
    source: str = "bbox"  # "outline" when the well loop was recovered
    treads: int = 0          # tread lines the drawing shows in this well
    tread_depth: float = 0.0  # spacing between them, along the run
    run_angle: float = 0.0    # radians, direction the flights travel
    flight_width: float = 0.0  # width of one flight (well width minus the gap)

    @property
    def center(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.bounds
        return ((x0 + x1) / 2, (y0 + y1) / 2)

    @property
    def area(self) -> float:
        x0, y0, x1, y1 = self.bounds
        return max(0.0, x1 - x0) * max(0.0, y1 - y0)

    def polygon(self) -> Polygon:
        return shapely_box(*self.bounds)

    def to_dict(self) -> dict[str, Any]:
        x0, y0, x1, y1 = self.bounds
        return {
            "bounds": [round(value, 1) for value in self.bounds],
            "width": round(x1 - x0, 1),
            "depth": round(y1 - y0, 1),
            "label": self.label,
            "source": self.source,
            "treads": self.treads,
            "treadDepth": round(self.tread_depth, 1),
            "flightWidth": round(self.flight_width, 1),
        }


def _children(entity: Any) -> list[Any]:
    raw = getattr(entity, "proxy_graphic", None)
    if not raw:
        return []
    try:
        from ezdxf.proxygraphic import ProxyGraphic

        return list(ProxyGraphic(raw).virtual_entities())
    except Exception:  # noqa: BLE001
        return []


def _segments(children: Iterable[Any]) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
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
                for a, b in zip(points, points[1:]):
                    segments.append((a, b))
                closed = bool(child.closed) if kind == "LWPOLYLINE" else bool(child.is_closed)
                if closed and len(points) > 2:
                    segments.append((points[-1], points[0]))
        except Exception:  # noqa: BLE001
            continue
    return [segment for segment in segments if segment[0] != segment[1]]


def _label(children: Iterable[Any]) -> str:
    parts = []
    for child in children:
        if child.dxftype() in {"TEXT", "MTEXT"}:
            try:
                value = child.plain_text() if child.dxftype() == "MTEXT" else child.dxf.text
                parts.append(str(value).strip())
            except Exception:  # noqa: BLE001
                pass
    return "".join(parts).replace(" ", "")


def _outline(segments: list[tuple[tuple[float, float], tuple[float, float]]]) -> Polygon | None:
    """The stair's outer loop, from the segment soup of its proxy graphic.

    A stair graphic is hundreds of tread lines; ``polygonize`` keeps only the
    loops they close, and the largest plausible one is the well. A single
    storey's stair well is a few square metres, so anything past 80 m² is a
    merged outline of the whole sheet rather than one stair.
    """
    if len(segments) < 3:
        return None
    from shapely.geometry import LineString
    from shapely.ops import polygonize

    lines = [LineString([a, b]) for a, b in segments]
    polygons = [polygon for polygon in polygonize(lines) if 1.0 < polygon.area <= 80_000_000.0]
    if not polygons:
        return None
    return max(polygons, key=lambda polygon: polygon.area)


def _extents(entity: Any, children: list[Any]) -> tuple[float, float, float, float] | None:
    try:
        from ezdxf import bbox as ezdxf_bbox

        extent = ezdxf_bbox.extents([entity])
        if extent.has_data:
            return (extent.extmin.x, extent.extmin.y, extent.extmax.x, extent.extmax.y)
    except Exception:  # noqa: BLE001
        pass
    points = [point for segment in _segments(children) for point in segment]
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _tread_stats(
    segments: list[tuple[tuple[float, float], tuple[float, float]]],
    bounds: tuple[float, float, float, float],
) -> tuple[int, float, float, float]:
    """``(treads, tread depth, run angle, flight width)`` from the tread lines.

    A stair's proxy graphic draws one line per tread. Those lines are the only
    honest source for how many steps there are and how deep they are: the riser
    follows from the storey height, but the run does not.
    """
    x0, y0, x1, y1 = bounds
    span = max(x1 - x0, y1 - y0)
    if span <= 0 or not segments:
        return 0, 0.0, 0.0, 0.0

    buckets: dict[int, list[tuple[tuple[float, float], tuple[float, float], float]]] = {}
    for a, b in segments:
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        # A tread line spans a flight, so it is a sizeable fraction of the well.
        if length < span * 0.25 or length > span * 1.15:
            continue
        angle = math.atan2(b[1] - a[1], b[0] - a[0]) % math.pi
        key = int(round(angle / (math.pi / 36)))  # 5-degree buckets
        buckets.setdefault(key, []).append((a, b, angle))
    if not buckets:
        return 0, 0.0, 0.0, 0.0

    lines = max(buckets.values(), key=len)
    if len(lines) < 3:
        return 0, 0.0, 0.0, 0.0
    angle = lines[0][2]
    nx, ny = -math.sin(angle), math.cos(angle)
    offsets = sorted(
        (a[0] + b[0]) / 2 * nx + (a[1] + b[1]) / 2 * ny for a, b, _angle in lines
    )
    distinct: list[float] = []
    for value in offsets:
        if not distinct or value - distinct[-1] > 25.0:
            distinct.append(value)
    gaps = [b - a for a, b in zip(distinct, distinct[1:]) if 150.0 <= b - a <= 400.0]
    depth = sorted(gaps)[len(gaps) // 2] if gaps else 0.0
    width = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b, _angle in lines) / len(lines)
    return len(distinct), depth, angle + math.pi / 2, width


def read_stairs(document: Any) -> list[StairWell]:
    """Every stair object in the drawing, as a footprint."""
    wells: list[StairWell] = []
    for entity in document.modelspace():
        if entity.dxftype() != "SWR_RECTSTAIR":
            continue
        children = _children(entity)
        if not children:
            continue
        segments = _segments(children)
        outline = _outline(segments)
        if outline is not None and min(outline.bounds[2] - outline.bounds[0],
                                       outline.bounds[3] - outline.bounds[1]) > 800:
            bounds = tuple(float(value) for value in outline.bounds)
            treads, depth, run, width = _tread_stats(segments, bounds)
            wells.append(
                StairWell(
                    bounds=bounds,
                    label=_label(children),
                    source="outline",
                    treads=treads,
                    tread_depth=depth,
                    run_angle=run,
                    flight_width=width,
                )
            )
            continue
        box = _extents(entity, children)
        if box is None:
            continue
        treads, depth, run, width = _tread_stats(segments, box)
        wells.append(
            StairWell(
                bounds=box,
                label=_label(children),
                source="bbox",
                treads=treads,
                tread_depth=depth,
                run_angle=run,
                flight_width=width,
            )
        )
    return wells


def stairs_in(
    wells: list[StairWell],
    bounds: tuple[float, float, float, float],
    *,
    pad: float = 500.0,
) -> list[StairWell]:
    x0, y0, x1, y1 = bounds
    kept = []
    for well in wells:
        cx, cy = well.center
        if x0 - pad <= cx <= x1 + pad and y0 - pad <= cy <= y1 + pad:
            kept.append(well)
    return kept


def align_wells(
    wells: list[StairWell],
    dx: float,
    dy: float,
    scale: float = 1.0,
) -> list[StairWell]:
    """Move wells onto a storey's common origin.

    Plans are drawn side by side on the sheet and translated onto one origin
    before they are stacked, so each storey's stairs have to move with it.
    """
    moved: list[StairWell] = []
    for well in wells:
        x0, y0, x1, y1 = well.bounds
        moved.append(
            StairWell(
                bounds=((x0 + dx) * scale, (y0 + dy) * scale, (x1 + dx) * scale, (y1 + dy) * scale),
                label=well.label,
                source=well.source,
            )
        )
    return moved


def stair_pieces(
    well: "StairWell",
    height: float,
    base_z: float,
    *,
    gap: float = 100.0,
    default_treads: int = 12,
    default_tread: float = 300.0,
) -> list[tuple[Polygon, float, float]]:
    """The two flights and their landing, as boxes ``(footprint, z0, z1)``.

    A dog-leg stair is two runs side by side travelling in opposite directions,
    with a landing at the far end and the floor at the near end. The tread count
    and depth come from the drawing when it draws them; the riser is the storey
    height split over both runs, because a plan does not show height.
    """
    x0, y0, x1, y1 = well.bounds
    length = max(x1 - x0, y1 - y0)
    width = min(x1 - x0, y1 - y0)
    treads = well.treads if well.treads >= 4 else default_treads
    tread = well.tread_depth if well.tread_depth >= 150 else default_tread
    flight_width = min(well.flight_width or (width - gap) / 2.0, (width - gap) / 2.0)
    if flight_width < 600:
        return []
    run = well.run_angle if well.tread_depth else (0.0 if x1 - x0 >= y1 - y0 else math.pi / 2)
    ux, uy = math.cos(run), math.sin(run)
    nx, ny = -uy, ux
    cx, cy = well.center
    riser = height / (2.0 * treads)
    flight_length = min(treads * tread, length)

    def box(s_a: float, s_b: float, lat_a: float, lat_b: float) -> Polygon:
        corners = [
            (cx + ux * s + nx * t, cy + uy * s + ny * t)
            for s, t in ((s_a, lat_a), (s_b, lat_a), (s_b, lat_b), (s_a, lat_b))
        ]
        polygon = Polygon(corners)
        return polygon if polygon.is_valid else polygon.buffer(0)

    pieces: list[tuple[Polygon, float, float]] = []
    half = flight_length / 2.0
    lateral = (gap + flight_width) / 2.0
    for side in (-1, 1):
        lat_a, lat_b = side * gap / 2.0, side * lateral
        for index in range(treads):
            if side < 0:
                s_a = -half + index * tread
                s_b = s_a + tread
                z0 = base_z
                z1 = base_z + (index + 1) * riser
            else:
                s_b = half - index * tread
                s_a = s_b - tread
                z0 = base_z + (treads + index) * riser
                z1 = base_z + (treads + index + 1) * riser
            if s_a < -length / 2 or s_b > length / 2:
                continue
            pieces.append((box(s_a, s_b, lat_a, lat_b), z0, z1))

    # The half landing at the far end, at the height where the first run tops out.
    landing_end = min(length / 2.0, half + max(1200.0, flight_width))
    pieces.append(
        (box(half, landing_end, -lateral, lateral), base_z, base_z + treads * riser)
    )
    return pieces


def slab_with_wells(plate: Polygon, wells: list[StairWell]) -> Polygon | None:
    """The plate with each stair well punched out of it.

    A well that misses the plate entirely changes nothing, so a plate with no
    stairs over it comes back as itself.
    """
    result = plate
    for well in wells:
        hole = well.polygon()
        if not hole.intersects(plate):
            continue
        try:
            cut = result.difference(hole)
        except Exception:  # noqa: BLE001 - a degenerate well must not lose the slab
            continue
        if cut.is_empty:
            continue
        parts = [part for part in getattr(cut, "geoms", [cut]) if isinstance(part, Polygon)]
        if not parts:
            continue
        result = max(parts, key=lambda part: part.area)
    return result
