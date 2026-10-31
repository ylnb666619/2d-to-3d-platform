"""Extract wall candidates from a floor plan.

Two strategies, because Chinese construction drawings draw walls two ways:

* **closed** — the wall body is a closed polyline or a hatch boundary;
* **pairs** — the wall is two parallel lines, and the wall is the space between
  them (the common case in 建施图).

Both produce :class:`WallSegment` records, and both report what they could not
explain. A silent guess is worse than a reported gap here: the whole point of
the massing stage is to see whether the reading is right.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from shapely.geometry import Polygon, box as shapely_box
from shapely.ops import unary_union

from .document import open_document, read_document
from .layers import classify, layers_for, load_config
from .model import Bounds, DocumentInfo, WallResult, WallSegment


@dataclass
class ExtractOptions:
    strategy: str = "auto"  # auto | closed | pairs
    min_thickness: float = 60.0
    max_thickness: float = 600.0
    min_length: float = 100.0
    angle_tolerance: float = 2.0
    snap_tolerance: float = 10.0
    region: Bounds | None = None


@dataclass
class _Line:
    x0: float
    y0: float
    x1: float
    y1: float
    layer: str
    handle: str
    angle: float = 0.0
    dx: float = 0.0
    dy: float = 0.0
    nx: float = 0.0
    ny: float = 0.0
    offset: float = 0.0
    t0: float = 0.0
    t1: float = 0.0
    used: bool = False

    @property
    def length(self) -> float:
        return self.t1 - self.t0


def _prepare(line: _Line) -> _Line:
    dx, dy = line.x1 - line.x0, line.y1 - line.y0
    length = math.hypot(dx, dy)
    if length <= 0:
        line.t1 = line.t0 = 0.0
        return line
    dx, dy = dx / length, dy / length
    angle = math.atan2(dy, dx)
    if angle < 0:
        angle += math.pi
    if angle >= math.pi:
        angle -= math.pi
    # Normal pointing consistently to one side of the normalised direction.
    nx, ny = -math.sin(angle), math.cos(angle)
    if nx < 0 or (nx == 0 and ny < 0):
        nx, ny = -nx, -ny
    line.angle, line.dx, line.dy, line.nx, line.ny = angle, dx, dy, nx, ny
    line.offset = line.x0 * nx + line.y0 * ny
    line.t0 = line.x0 * dx + line.y0 * dy
    line.t1 = line.x1 * dx + line.y1 * dy
    if line.t0 > line.t1:
        line.t0, line.t1 = line.t1, line.t0
    return line


def _in_region(bounds: Bounds | None, region: Bounds | None) -> bool:
    if region is None or bounds is None:
        return True
    return not (
        bounds[2] < region[0]
        or bounds[0] > region[2]
        or bounds[3] < region[1]
        or bounds[1] > region[3]
    )


def _entity_bounds(entity: Any) -> Bounds | None:
    try:
        from ezdxf import bbox as ezdxf_bbox

        extent = ezdxf_bbox.extents([entity])
        if extent.has_data:
            return (extent.extmin.x, extent.extmin.y, extent.extmax.x, extent.extmax.y)
    except Exception:  # noqa: BLE001
        return None
    return None


def _collect_lines(document: Any, layers: set[str], region: Bounds | None) -> list[_Line]:
    lines: list[_Line] = []

    def add(x0: float, y0: float, x1: float, y1: float, layer: str, handle: str) -> None:
        if math.hypot(x1 - x0, y1 - y0) < 1e-6:
            return
        if _in_region((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)), region):
            lines.append(_prepare(_Line(x0, y0, x1, y1, layer, handle)))

    for entity in document.modelspace():
        layer = str(getattr(entity.dxf, "layer", "0"))
        if layer not in layers:
            continue
        kind = entity.dxftype()
        handle = str(getattr(entity.dxf, "handle", ""))
        if kind == "LINE":
            add(float(entity.dxf.start.x), float(entity.dxf.start.y),
                float(entity.dxf.end.x), float(entity.dxf.end.y), layer, handle)
        elif kind in {"LWPOLYLINE", "POLYLINE"}:
            points = (
                [(float(x), float(y)) for x, y, _b in entity.get_points("xyb")]
                if kind == "LWPOLYLINE"
                else [(float(v.x), float(v.y)) for v in entity.points()]
            )
            closed = bool(entity.closed) if kind == "LWPOLYLINE" else bool(entity.is_closed)
            for (x0, y0), (x1, y1) in zip(points, points[1:]):
                add(x0, y0, x1, y1, layer, handle)
            if closed and len(points) > 2:
                add(points[-1][0], points[-1][1], points[0][0], points[0][1], layer, handle)
    return lines


def _pair_lines(lines: list[_Line], options: ExtractOptions) -> list[WallSegment]:
    """Pair parallel lines whose spacing looks like a wall thickness."""
    buckets: dict[int, list[_Line]] = defaultdict(list)
    for line in lines:
        if line.length <= 0:
            continue
        key = int(round(math.degrees(line.angle) / options.angle_tolerance))
        buckets[key].append(line)

    segments: list[WallSegment] = []
    for bucket in buckets.values():
        bucket.sort(key=lambda item: item.offset)
        for i, first in enumerate(bucket):
            if first.used:
                continue
            for second in bucket[i + 1:]:
                thickness = second.offset - first.offset
                if thickness > options.max_thickness:
                    break
                if thickness < options.min_thickness:
                    continue
                overlap_start = max(first.t0, second.t0)
                overlap_end = min(first.t1, second.t1)
                overlap = overlap_end - overlap_start
                if overlap < options.min_length:
                    continue
                if overlap < 0.5 * min(first.length, second.length):
                    continue
                mid_offset = (first.offset + second.offset) / 2
                dx, dy = first.dx, first.dy
                # Rebuild from the shared frame: direction (dx,dy), normal (nx,ny).
                nx, ny = first.nx, first.ny
                sx = dx * overlap_start + nx * mid_offset
                sy = dy * overlap_start + ny * mid_offset
                ex = dx * overlap_end + nx * mid_offset
                ey = dy * overlap_end + ny * mid_offset
                segments.append(
                    WallSegment(
                        start=(sx, sy),
                        end=(ex, ey),
                        thickness=thickness,
                        source="pairs",
                        layer=first.layer if first.layer == second.layer else f"{first.layer}|{second.layer}",
                        handles=[first.handle, second.handle],
                    )
                )
                first.used = True
                second.used = True
                break
    return segments


def _closed_polygons(document: Any, layers: set[str], region: Bounds | None) -> tuple[list[Polygon], int]:
    polygons: list[Polygon] = []
    considered = 0
    for entity in document.modelspace():
        layer = str(getattr(entity.dxf, "layer", "0"))
        if layer not in layers:
            continue
        kind = entity.dxftype()
        if kind not in {"LWPOLYLINE", "POLYLINE"}:
            continue
        closed = bool(entity.closed) if kind == "LWPOLYLINE" else bool(entity.is_closed)
        if not closed:
            continue
        points = (
            [(float(x), float(y)) for x, y, _b in entity.get_points("xyb")]
            if kind == "LWPOLYLINE"
            else [(float(v.x), float(v.y)) for v in entity.points()]
        )
        if len(points) < 3:
            continue
        if not _in_region(
            (min(p[0] for p in points), min(p[1] for p in points),
             max(p[0] for p in points), max(p[1] for p in points)),
            region,
        ):
            continue
        considered += 1
        polygon = Polygon(points)
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if polygon.is_empty:
            continue
        polygons.append(polygon)
    return polygons, considered


def _hatch_polygons(document: Any, layers: set[str], region: Bounds | None) -> list[Polygon]:
    """Hatch boundaries on wall layers, when the drawing fills walls instead."""
    from ezdxf import const

    polygons: list[Polygon] = []
    for entity in document.modelspace():
        if entity.dxftype() != "HATCH":
            continue
        layer = str(getattr(entity.dxf, "layer", "0"))
        if layer not in layers:
            continue
        if not _in_region(_entity_bounds(entity), region):
            continue
        for path in entity.paths:
            if path.type != const.BOUNDARY_PATH_POLYLINE:
                continue
            points = [(float(v[0]), float(v[1])) for v in path.vertices]
            if len(points) < 3:
                continue
            polygon = Polygon(points)
            if not polygon.is_valid:
                polygon = polygon.buffer(0)
            if not polygon.is_empty:
                polygons.append(polygon)
    return polygons


def _merge_segments(segments: list[WallSegment], tolerance: float) -> list[WallSegment]:
    """Join collinear runs of one wall so the massing is not hundreds of slivers."""
    merged: list[WallSegment] = []
    for segment in sorted(segments, key=lambda item: (-item.thickness, item.start)):
        placed = False
        for existing in merged:
            if abs(existing.thickness - segment.thickness) > 1.0:
                continue
            if not _collinear(existing, segment, tolerance):
                continue
            if _extend(existing, segment, tolerance):
                placed = True
                break
        if not placed:
            merged.append(segment)
    return merged


def _direction(segment: WallSegment) -> tuple[float, float, float]:
    dx = segment.end[0] - segment.start[0]
    dy = segment.end[1] - segment.start[1]
    length = math.hypot(dx, dy) or 1.0
    return dx / length, dy / length, length


def _collinear(a: WallSegment, b: WallSegment, tolerance: float) -> bool:
    ax, ay, _ = _direction(a)
    bx, by, _ = _direction(b)
    cross = abs(ax * by - ay * bx)
    if cross > 0.02:  # about 1 degree
        return False
    # Distance from b's midpoint to a's infinite line.
    mx, my = b.midpoint
    distance = abs((mx - a.start[0]) * ay - (my - a.start[1]) * ax)
    return distance <= max(tolerance, a.thickness)


def _extend(target: WallSegment, source: WallSegment, tolerance: float) -> bool:
    ax, ay, alen = _direction(target)
    def project(point: tuple[float, float]) -> float:
        return (point[0] - target.start[0]) * ax + (point[1] - target.start[1]) * ay

    t_start, t_end = 0.0, alen
    s0, s1 = sorted((project(source.start), project(source.end)))
    if s1 < -tolerance or s0 > alen + tolerance:
        return False
    new_start = target.start if s0 > 0 else source.start if s0 < 0 else target.start
    new_end = target.end if s1 < alen else source.end if s1 > alen else target.end
    target.start, target.end = new_start, new_end
    target.handles = sorted(set(target.handles) | set(source.handles))
    return True


def _thickness_stats(segments: list[WallSegment]) -> dict[str, Any]:
    if not segments:
        return {}
    values = sorted(round(segment.thickness / 5) * 5 for segment in segments)
    counts = Counter(values)
    common = counts.most_common(6)
    return {
        "min": values[0],
        "max": values[-1],
        "median": values[len(values) // 2],
        "common": [{"thickness": int(value), "count": int(count)} for value, count in common],
    }


PROXY_TYPES = ("SWR_WALL", "SWR_COLUMN", "SWR_OPENING")


def _proxy_children(entity: Any) -> list[Any]:
    """The proxy-graphic primitives a custom CAD object carries.

    ZWCAD Architecture (中望建筑) stores walls, columns, doors and windows as
    custom objects. The DXF export keeps a proxy graphic — the primitives
    another CAD system draws when it has no object enabler — and that graphic
    is the object's real geometry: both wall faces, its centreline, and the
    column's outline. Reading it beats guessing from stray lines.
    """
    raw = getattr(entity, "proxy_graphic", None)
    if not raw:
        return []
    try:
        from ezdxf.proxygraphic import ProxyGraphic

        return list(ProxyGraphic(raw).virtual_entities())
    except Exception:  # noqa: BLE001 - an unreadable proxy falls back to lines
        return []


def _child_segments(children: Iterable[Any]) -> list[tuple[tuple[float, float], tuple[float, float]]]:
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


def _cluster(values: list[float], tolerance: float) -> list[float]:
    """Collapse near-equal offsets; returns one representative per cluster."""
    if not values:
        return []
    ordered = sorted(values)
    clusters = [ordered[0]]
    for value in ordered[1:]:
        if value - clusters[-1] > tolerance:
            clusters.append(value)
    return clusters


def _wall_bodies(
    segments: list[tuple[tuple[float, float], tuple[float, float]]],
    *,
    layer: str,
    handle: str,
    min_thickness: float,
    max_thickness: float,
) -> list[tuple[WallSegment, Polygon]]:
    """One custom wall object → its rectangular bodies.

    The proxy graphic holds both faces plus a centreline that overshoots at the
    joints. The faces give the thickness (their offset spread) and the extents
    (their projection intervals); each disjoint interval is one body, so a wall
    broken by an opening stays broken instead of being silently bridged.
    """
    if len(segments) < 2:
        return []
    longest = max(segments, key=lambda seg: math.dist(seg[0], seg[1]))
    (x0, y0), (x1, y1) = longest
    length = math.hypot(x1 - x0, y1 - y0)
    if length <= 0:
        return []
    ux, uy = (x1 - x0) / length, (y1 - y0) / length
    nx, ny = -uy, ux

    parallel: list[tuple[float, float, float]] = []
    for (a, b) in segments:
        vx, vy = b[0] - a[0], b[1] - a[1]
        span = math.hypot(vx, vy)
        if span <= 0:
            continue
        if abs(ux * vy - uy * vx) / span > 0.02:  # ~1 degree off-axis
            continue
        t0 = a[0] * ux + a[1] * uy
        t1 = b[0] * ux + b[1] * uy
        offset = a[0] * nx + a[1] * ny
        parallel.append((offset, min(t0, t1), max(t0, t1)))
    if len(parallel) < 2:
        return []

    offsets = _cluster([item[0] for item in parallel], 5.0)
    if len(offsets) < 2:
        return []
    lo, hi = offsets[0], offsets[-1]
    thickness = hi - lo
    if not (min_thickness <= thickness <= max_thickness):
        return []
    center = (lo + hi) / 2

    intervals: list[tuple[float, float]] = []
    for offset, t0, t1 in parallel:
        if abs(offset - lo) <= 3.0 or abs(offset - hi) <= 3.0:
            intervals.append((t0, t1))
    intervals.sort()
    merged: list[tuple[float, float]] = []
    for t0, t1 in intervals:
        if merged and t0 <= merged[-1][1] + 1e-6:
            merged[-1] = (merged[-1][0], max(merged[-1][1], t1))
        else:
            merged.append((t0, t1))

    def point(t: float, offset: float) -> tuple[float, float]:
        return (t * ux + offset * nx, t * uy + offset * ny)

    bodies: list[tuple[WallSegment, Polygon]] = []
    for t0, t1 in merged:
        if t1 - t0 < 1.0:
            continue
        polygon = Polygon(
            [point(t0, lo), point(t1, lo), point(t1, hi), point(t0, hi)]
        )
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        if polygon.is_empty:
            continue
        bodies.append(
            (
                WallSegment(
                    start=point(t0, center),
                    end=point(t1, center),
                    thickness=thickness,
                    source="proxy",
                    layer=layer,
                    handles=[handle],
                ),
                polygon,
            )
        )
    return bodies


def _column_polygon(
    segments: list[tuple[tuple[float, float], tuple[float, float]]],
) -> Polygon | None:
    """A column's footprint from the closed loop in its proxy graphic.

    A convex hull is wrong here: a column object occasionally carries stray
    geometry (a label leader, a reference point), and the hull of that is a
    polygon covering half the sheet. The closed loop the four edges form is the
    column itself, so take the loop.
    """
    if len(segments) < 3:
        return None
    from shapely.geometry import LineString
    from shapely.ops import polygonize

    # The proxy graphic of a column repeats its outline and often carries a
    # leader back to the block's origin -- a segment tens of metres long at a
    # sheet where a column is half a metre. Neither survives this: keep the
    # short edges, drop the duplicates, and the loop that is left is the
    # column. Without it, 221 of this drawing's 285 columns were discarded as
    # "no closed loop" and every floor above the ground one lost its columns.
    short = [
        (a, b)
        for a, b in segments
        if ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5 <= 3_000.0
    ]
    unique: list[tuple[tuple[float, float], tuple[float, float]]] = []
    seen: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    for a, b in short:
        key = tuple(sorted(((round(a[0]), round(a[1])), (round(b[0]), round(b[1])))))
        if key in seen:
            continue
        seen.add(key)
        unique.append((a, b))
    if len(unique) < 3:
        return None

    lines = [LineString([a, b]) for a, b in unique]
    polygons = [polygon for polygon in polygonize(lines) if polygon.area > 1]
    if not polygons:
        return None
    # A structural column is well under 4 m^2; anything larger is a stray loop.
    plausible = [polygon for polygon in polygons if polygon.area <= 4_000_000]
    if not plausible:
        return None
    return max(plausible, key=lambda polygon: polygon.area)


def _proxy_objects(
    document: Any,
    options: ExtractOptions,
) -> tuple[list[tuple[WallSegment, Polygon]], list[Polygon], int]:
    """Walls and columns carried as custom CAD objects, plus the opening count."""
    walls: list[tuple[WallSegment, Polygon]] = []
    columns: list[Polygon] = []
    openings = 0
    for entity in document.modelspace():
        kind = entity.dxftype()
        if kind not in PROXY_TYPES:
            continue
        if not _in_region(_entity_bounds(entity), options.region):
            continue
        layer = str(getattr(entity.dxf, "layer", "0"))
        handle = str(getattr(entity.dxf, "handle", ""))
        segments = _child_segments(_proxy_children(entity))
        if not segments:
            continue
        if kind == "SWR_WALL":
            walls.extend(
                _wall_bodies(
                    segments,
                    layer=layer,
                    handle=handle,
                    min_thickness=options.min_thickness,
                    max_thickness=options.max_thickness,
                )
            )
        elif kind == "SWR_COLUMN":
            polygon = _column_polygon(segments)
            if polygon is None:
                # Some column objects draw no closed loop — only the four edges
                # as separate lines that do not quite meet. Their extents are
                # still the column, so fall back to them rather than losing
                # every column on the plan.
                xs = [point[0] for segment in segments for point in segment]
                ys = [point[1] for segment in segments for point in segment]
                if xs and ys:
                    from shapely.geometry import box as shapely_box

                    candidate = shapely_box(min(xs), min(ys), max(xs), max(ys))
                    if 0.0 < candidate.area <= 4_000_000.0:
                        polygon = candidate
            if polygon is not None:
                columns.append(polygon)
        elif kind == "SWR_OPENING":
            openings += 1
    return walls, columns, openings


def polygons_from_segments(segments: Iterable[WallSegment]) -> list[Polygon]:
    polygons: list[Polygon] = []
    for segment in segments:
        if segment.length <= 0 or segment.thickness <= 0:
            continue
        from shapely.geometry import LineString

        area = LineString([segment.start, segment.end]).buffer(
            segment.thickness / 2, cap_style="flat", join_style="mitre"
        )
        if not area.is_empty:
            polygons.append(area)
    return polygons


def extract(
    path: str,
    *,
    config_path: str | None = None,
    options: ExtractOptions | None = None,
) -> WallResult:
    """Read a drawing and return wall candidates plus the evidence for them."""
    options = options or ExtractOptions()
    config = load_config(config_path)
    document, open_warnings = open_document(path)
    try:
        info = read_document(path, document=document, warnings=open_warnings, close=False)
        classify(info, config)
        wall_layers = set(layers_for(info, "wall"))
        warnings = list(info.warnings)

        # Custom CAD objects first: when the drawing carries real wall objects,
        # their proxy geometry is exact and no line-pairing guess is needed.
        proxy_walls: list[tuple[WallSegment, Polygon]] = []
        proxy_columns: list[Polygon] = []
        opening_count = 0
        if options.strategy in ("auto", "proxy"):
            proxy_walls, proxy_columns, opening_count = _proxy_objects(document, options)

        if not wall_layers and not proxy_walls:
            return WallResult(
                segments=[],
                stats={"wallLayers": []},
                warnings=warnings
                + ["没有识别到任何墙体图层或墙体对象，请检查图层配置。"],
            )

        if proxy_walls:
            strategies: list[str] = []
        else:
            strategies = ["closed", "pairs"] if options.strategy == "auto" else [options.strategy]

        polygons: list[Polygon] = [polygon for _segment, polygon in proxy_walls] + list(proxy_columns)
        segments: list[WallSegment] = [segment for segment, _polygon in proxy_walls]
        considered = 0
        closed_polygons: list[Polygon] = []

        if "closed" in strategies:
            closed_polygons, considered = _closed_polygons(document, wall_layers, options.region)
            hatch_polygons = _hatch_polygons(document, wall_layers, options.region)
            polygons.extend(closed_polygons)
            polygons.extend(hatch_polygons)
            if not closed_polygons and not hatch_polygons:
                warnings.append("墙体图层上没有闭合多段线或填充边界，改用平行线配对。")

        if "pairs" in strategies:
            lines = _collect_lines(document, wall_layers, options.region)
            total_length = sum(line.length for line in lines)
            pair_segments = _pair_lines(lines, options)
            used_length = sum(
                line.length
                for line in lines
                if line.used
            )
            pair_segments = _merge_segments(pair_segments, options.snap_tolerance)
            segments.extend(pair_segments)
            polygons.extend(polygons_from_segments(pair_segments))
            coverage = (used_length / total_length) if total_length else 0.0
        else:
            coverage = None
            total_length = 0

        merged_polygons: list[Polygon] = []
        if polygons:
            union = unary_union(polygons)
            merged_polygons = [
                geom for geom in getattr(union, "geoms", [union]) if isinstance(geom, Polygon) and geom.area > 1
            ]

        stats = {
            "wallLayers": sorted(wall_layers),
            "strategy": options.strategy,
            "source": "proxy" if proxy_walls else "geometry",
            "proxyWalls": len(proxy_walls),
            "proxyColumns": len(proxy_columns),
            "openings": opening_count,
            "closedProfiles": len(closed_polygons),
            "segments": len(segments),
            "polygons": len(merged_polygons),
            "totalPolygonArea": round(sum(polygon.area for polygon in merged_polygons), 1),
            "thickness": _thickness_stats(segments),
            "pairCoverage": None if coverage is None else round(coverage, 3),
            "lineLength": round(total_length, 1),
            "region": list(options.region) if options.region else None,
        }
        if coverage is not None and coverage < 0.4 and not closed_polygons:
            warnings.append(
                f"平行线配对只覆盖了 {coverage:.0%} 的墙体线，模型可能不完整；"
                "请查看 overlay 对比图确认。"
            )
        return WallResult(
            segments=segments,
            polygons=merged_polygons,
            columns=proxy_columns,
            stats=stats,
            warnings=warnings,
        )
    finally:
        try:
            document.close()
        except Exception:  # noqa: BLE001
            pass


def region_bounds(info: DocumentInfo, selector: str | None) -> Bounds | None:
    """Resolve ``--region N`` or ``--region bbox:x0,y0,x1,y1``."""
    if not selector:
        return None
    text = selector.strip()
    if text.lower().startswith("bbox:"):
        parts = [piece for piece in text[5:].replace(";", ",").split(",") if piece.strip()]
        if len(parts) != 4:
            raise ValueError("bbox 需要四个数：bbox:x0,y0,x1,y1")
        return tuple(float(piece) for piece in parts)  # type: ignore[return-value]
    try:
        index = int(text)
    except ValueError:
        raise ValueError(f"无法识别的区域选择：{selector}") from None
    for region in info.regions:
        if region.index == index:
            return region.bounds
    raise ValueError(f"图纸只有 {len(info.regions)} 个区域，没有第 {index} 个。")
