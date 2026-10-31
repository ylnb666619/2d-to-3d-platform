"""Assemble floors into a building: walls per storey, slabs, roof.

The 建施图 names each plan ("二层平面图") near it and carries an elevation
chain elsewhere. Those two facts are enough to place each plan at its real
height instead of stacking them by draw order.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from shapely.geometry import Polygon
from shapely.ops import unary_union

from .floors import FloorGroup
from .model import TextItem

FLOOR_WORDS = {
    "一": 1, "首": 1, "底": 1,
    "二": 2, "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
}
_PLAN_TITLE = re.compile(r"([一二三四五六七八九十首底两]+)\s*层\s*平面")
_ROOF_TITLE = re.compile(r"(屋顶|屋面)\s*(层)?\s*平面")


@dataclass
class FloorMatch:
    group: FloorGroup
    floor: int | None
    title: str
    distance: float
    is_roof: bool = False


def _centroid_distance(text: TextItem, group: FloorGroup) -> float:
    if not text.position:
        return float("inf")
    x0, y0, x1, y1 = group.bounds
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return ((text.position[0] - cx) ** 2 + (text.position[1] - cy) ** 2) ** 0.5


def match_floors(
    texts: list[TextItem],
    groups: list[FloorGroup],
    *,
    max_distance: float = 60_000.0,
) -> list[FloorMatch]:
    """Attach each wall group to the plan title nearest to it.

    Titles sit in the sheet's title block, roughly one plan-width away from the
    geometry, so the match is by nearest title rather than by containment.
    """
    titles = []
    for text in texts:
        value = (text.text or "").strip()
        if "大样" in value or "详图" in value:
            continue
        roof = _ROOF_TITLE.search(value)
        match = _PLAN_TITLE.search(value)
        if roof:
            titles.append((text, None, value, True))
        elif match:
            words = match.group(1)
            number = FLOOR_WORDS.get(words)
            if number is None and len(words) == 1:
                number = FLOOR_WORDS.get(words)
            titles.append((text, number, value, False))

    matches: list[FloorMatch] = []
    for group in groups:
        best: FloorMatch | None = None
        for text, floor, value, is_roof in titles:
            distance = _centroid_distance(text, group)
            if best is None or distance < best.distance:
                best = FloorMatch(group=group, floor=floor, title=value, distance=distance, is_roof=is_roof)
        if best is None:
            best = FloorMatch(group=group, floor=None, title="", distance=float("inf"))
        if best.distance > max_distance:
            best.floor = None
            best.is_roof = False
        matches.append(best)
    return matches


def slab_polygons(
    walls: list[Polygon],
    *,
    close_mm: float = 1500.0,
    min_area: float = 1.0,
) -> list[Polygon]:
    """The floor plates of one storey.

    Walls arrive as separate rectangles that do not touch at their junctions,
    so the union is a cloud of pieces, not a closed ring. Closing the union by
    ``close_mm`` joins the network, and filling each resulting exterior gives
    the plate: the interior rooms are floor, not holes. A storey drawn as two
    separate wings returns two plates.
    """
    if not walls:
        return []
    merged = unary_union(walls)
    closed = merged.buffer(close_mm, join_style="mitre").buffer(-close_mm, join_style="mitre")
    if closed.is_empty:
        return []
    parts = [part for part in getattr(closed, "geoms", [closed]) if isinstance(part, Polygon)]
    plates: list[Polygon] = []
    for part in parts:
        if len(part.exterior.coords) < 3:
            continue
        filled = Polygon(part.exterior)  # rooms are floor; the shell is the edge
        if filled.is_valid and filled.area > min_area:
            plates.append(filled)
    return plates


def slab_polygon(walls: list[Polygon], *, close_mm: float = 1500.0) -> Polygon | None:
    """The largest plate, for callers that can only take one."""
    plates = slab_polygons(walls, close_mm=close_mm)
    return max(plates, key=lambda plate: plate.area) if plates else None


@dataclass
class Storey:
    number: int
    walls: list[Polygon]
    base: float
    height: float
    title: str = ""
    center: tuple[float, float] = (0.0, 0.0)
    bounds: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    # Each storey may come from its own DXF (a ground floor drawn in a separate
    # file), so its openings, wall centrelines and stairs travel with it.
    openings: list[Any] = field(default_factory=list)
    segments: list[Any] = field(default_factory=list)
    stairs: list[Any] = field(default_factory=list)
    axes: list[Any] = field(default_factory=list)
    columns: list[Any] = field(default_factory=list)
    rooms: list[Any] = field(default_factory=list)


@dataclass
class BuildingPlan:
    storeys: list[Storey] = field(default_factory=list)
    slab_thickness: float = 120.0
    roof_thickness: float = 150.0
    # A parapet is not dimensioned on the plan; 1200 is the usual height for a
    # school roof and is reported as an assumption rather than passed off as read.
    parapet_height: float = 1200.0
    parapet_thickness: float = 200.0
    notes: list[str] = field(default_factory=list)

    def report(self) -> dict[str, Any]:
        return {
            "storeys": [
                {
                    "floor": storey.number,
                    "title": storey.title,
                    "base": storey.base,
                    "height": storey.height,
                    "walls": len(storey.walls),
                }
                for storey in self.storeys
            ],
            "notes": self.notes,
        }


def _stair_centre(storey: "Storey") -> tuple[float, float] | None:
    if not storey.stairs:
        return None
    xs = [well.center[0] for well in storey.stairs]
    ys = [well.center[1] for well in storey.stairs]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _overlap(reference_union: Any, target_union: Any, dx: float, dy: float) -> float:
    """How much of the smaller storey's wall area lands on the other's."""
    from shapely.affinity import translate

    if reference_union.is_empty or target_union.is_empty:
        return 0.0
    try:
        moved = translate(target_union, xoff=dx, yoff=dy)
        intersection = reference_union.intersection(moved).area
    except Exception:  # noqa: BLE001 - a broken union must not stop the build
        return 0.0
    smaller = min(reference_union.area, target_union.area)
    return float(intersection / smaller) if smaller > 0 else 0.0


def storey_offsets(
    plan: BuildingPlan,
) -> tuple[dict[int, tuple[float, float]], dict[int, str], dict[int, float]]:
    """How far each storey must move to sit on the first one, and why.

    Four datums are tried and each is *scored* by how much wall it lands on
    wall, rather than one being trusted outright:

    * **axis labels** — exact when both plans carry the same numbers;
    * **wall overlap** — the cross-correlation peak of the two wall rasters;
    * **stair wells** — a shaft is at the same place on every floor, but only
      when both drawings model the whole well;
    * **bounding-box centre** — the last resort, because the ground floor
      carries steps, a ramp or a canopy that the upper floors do not.

    Stairs were trusted outright once and were wrong: the ground-floor drawing
    models one flight where the upper plans model the whole well, so their
    centres differ by metres.
    """
    from .align import offset as raster_offset
    from .axes import align_offset

    offsets: dict[int, tuple[float, float]] = {}
    sources: dict[int, str] = {}
    scores: dict[int, float] = {}
    if not plan.storeys:
        return offsets, sources, scores

    reference = plan.storeys[0]
    offsets[reference.number] = (0.0, 0.0)
    sources[reference.number] = "基准"
    scores[reference.number] = 1.0
    if len(plan.storeys) == 1:
        return offsets, sources, scores

    reference_union = unary_union(reference.walls)
    reference_stairs = _stair_centre(reference)

    for storey in plan.storeys[1:]:
        candidates: list[tuple[str, tuple[float, float]]] = []

        if reference.axes and storey.axes:
            found = align_offset(reference.axes, storey.axes)
            if found is not None:
                candidates.append(("轴号", found))

        correlation = raster_offset(reference.walls, storey.walls)
        if correlation is not None:
            candidates.append(("墙体重合", correlation[0]))

        if reference_stairs is not None:
            stairs = _stair_centre(storey)
            if stairs is not None:
                candidates.append(
                    ("楼梯", (reference_stairs[0] - stairs[0], reference_stairs[1] - stairs[1]))
                )

        candidates.append(
            (
                "包围盒中心",
                (reference.center[0] - storey.center[0], reference.center[1] - storey.center[1]),
            )
        )

        storey_union = unary_union(storey.walls)
        best_source = candidates[-1][0]
        best_offset = candidates[-1][1]
        best_score = -1.0
        for source, offset in candidates:
            score = _overlap(reference_union, storey_union, offset[0], offset[1])
            # Candidates are listed best-first, so a tie keeps the earlier one.
            if score > best_score + 1e-6:
                best_score, best_source, best_offset = score, source, offset
        offsets[storey.number] = best_offset
        sources[storey.number] = best_source
        scores[storey.number] = round(best_score, 3)
    return offsets, sources, scores


def plan_from_matches(
    matches: list[FloorMatch],
    elevations: list[float],
    *,
    default_height: float,
    assume_ground: bool = True,
    openings: list[Any] | None = None,
    segments: list[Any] | None = None,
    stairs: list[Any] | None = None,
    axes: list[Any] | None = None,
    columns: list[Any] | None = None,
    texts: list[Any] | None = None,
) -> BuildingPlan:
    """Turn matched plans into storeys at their elevations.

    Elevations come from the drawing's own level chain: the first gap may be
    taller than the rest. A floor with no plan of its own reuses the nearest
    lower plan, and the report says so — never silently.
    """
    plan = BuildingPlan()
    floors: dict[int, FloorMatch] = {}
    for match in matches:
        if match.floor is None:
            continue
        floors.setdefault(match.floor, match)

    if not floors:
        plan.notes.append("没有任何平面被标题识别出来，无法装配楼层。")
        return plan

    top = max(floors)
    for number in range(1, top + 1):
        match = floors.get(number)
        if match is None:
            if not assume_ground:
                plan.notes.append(f"缺少第 {number} 层平面，已跳过。")
                continue
            # Borrow the nearest plan by floor number. A missing ground floor
            # has no lower neighbour, so "nearest" matters, not "below".
            borrowed = min(floors.items(), key=lambda item: abs(item[0] - number))[1]
            match = borrowed
            plan.notes.append(f"第 {number} 层没有平面，借用「{borrowed.title}」的墙体（假定）。")

        if number - 1 < len(elevations) - 1:
            base = elevations[number - 1]
            height = elevations[number] - elevations[number - 1]
        else:
            base = (number - 1) * default_height if not elevations else elevations[-1]
            height = default_height
        x0, y0, x1, y1 = match.group.bounds
        storey = Storey(
            number=number,
            walls=list(match.group.polygons),
            base=base,
            height=height,
            title=match.title,
            center=((x0 + x1) / 2, (y0 + y1) / 2),
            bounds=(x0, y0, x1, y1),
        )
        if segments:
            storey.segments = [
                segment
                for segment in segments
                if x0 - 500 <= segment.midpoint[0] <= x1 + 500
                and y0 - 500 <= segment.midpoint[1] <= y1 + 500
            ]
        if openings:
            from .openings import openings_in

            storey.openings = openings_in(openings, storey.bounds)
        if stairs:
            from .stairs import stairs_in

            storey.stairs = stairs_in(stairs, storey.bounds)
        if axes:
            from .axes import labels_in

            storey.axes = labels_in(axes, storey.bounds)
        if columns:
            storey.columns = [
                polygon
                for polygon in columns
                if x0 - 500 <= polygon.centroid.x <= x1 + 500
                and y0 - 500 <= polygon.centroid.y <= y1 + 500
            ]
        if texts:
            from .recognize import find_rooms

            near = [
                text
                for text in texts
                if not text.position
                or (
                    x0 - 3000 <= text.position[0] <= x1 + 3000
                    and y0 - 3000 <= text.position[1] <= y1 + 3000
                )
            ]
            storey.rooms = find_rooms(storey.walls, near)
        plan.storeys.append(storey)
    return plan


def _cut_openings(
    bd: Any,
    shapes: list[Any],
    storey: Storey,
    openings: list[Any] | None,
    segments: list[Any] | None,
    dx: float,
    dy: float,
    scale: float,
) -> int:
    """Subtract each door and window prism from the walls it meets."""
    if not openings and not storey.openings:
        return 0
    from .openings import openings_in, place_openings

    x0, y0, x1, y1 = storey.bounds
    storey_segments = storey.segments
    if not storey_segments and segments:
        storey_segments = [
            segment
            for segment in segments
            if x0 - 500 <= segment.midpoint[0] <= x1 + 500 and y0 - 500 <= segment.midpoint[1] <= y1 + 500
        ]
    if not storey_segments:
        return 0
    source_openings = storey.openings or openings_in(openings or [], storey.bounds)
    placed = place_openings(source_openings, storey_segments)
    if not placed:
        return 0

    cutters = []
    for opening, _wall, center, angle in placed:
        sill = opening.sill
        top = min(sill + opening.height, storey.height - 20.0)
        if top - sill < 100.0:
            continue
        cutter = (
            bd.Pos(
                (center[0] + dx) * scale,
                (center[1] + dy) * scale,
                storey.base + (sill + top) / 2.0,
            )
            * bd.Rot(0, 0, math.degrees(angle))
            * bd.Box(opening.width, 400.0, top - sill)
        )
        cutters.append(cutter)
    if not cutters:
        return 0

    cut = 0
    for index, shape in enumerate(shapes):
        try:
            box = shape.bounding_box()
        except Exception:  # noqa: BLE001
            continue
        current = shape
        for cutter in cutters:
            try:
                cbox = cutter.bounding_box()
            except Exception:  # noqa: BLE001
                continue
            if (
                box.max.X < cbox.min.X - 1
                or cbox.max.X < box.min.X - 1
                or box.max.Y < cbox.min.Y - 1
                or cbox.max.Y < box.min.Y - 1
                or box.max.Z < cbox.min.Z - 1
                or cbox.max.Z < box.min.Z - 1
            ):
                continue
            try:
                result = current - cutter
            except Exception:  # noqa: BLE001
                continue
            if result is not None:
                current = result
                cut += 1
        shapes[index] = current
    return cut


def _panel_solids(
    bd: Any,
    storey: Storey,
    openings: list[Any] | None,
    segments: list[Any] | None,
    dx: float,
    dy: float,
    scale: float,
) -> list[Any]:
    """The door leaf or the glass, filling each opening.

    A hole in a wall is not a window. The panel is inset 60 mm all round and
    thinner than the wall, so the reveal shows and the opening reads as a
    recessed door or window rather than a filled rectangle.
    """
    from .openings import openings_in, place_openings

    if not openings and not storey.openings:
        return []
    x0, y0, x1, y1 = storey.bounds
    storey_segments = storey.segments or [
        segment
        for segment in (segments or [])
        if x0 - 500 <= segment.midpoint[0] <= x1 + 500
        and y0 - 500 <= segment.midpoint[1] <= y1 + 500
    ]
    if not storey_segments:
        return []
    placed = place_openings(storey.openings or openings_in(openings or [], storey.bounds), storey_segments)

    panels = []
    for opening, _wall, centre, angle in placed:
        is_door = opening.kind in {"door", "fire_door"}
        inset = 120.0
        panel_w = opening.width - inset
        panel_h = opening.height - inset
        if panel_w < 200 or panel_h < 200:
            continue
        if opening.sill + opening.height > storey.height + 1:
            continue
        thickness = 40.0 if is_door else 24.0
        place = bd.Pos(
            (centre[0] + dx) * scale,
            (centre[1] + dy) * scale,
            storey.base + opening.sill + opening.height / 2.0,
        ) * bd.Rot(0, 0, math.degrees(angle))
        kind = "门" if is_door else "窗"
        name = f"{kind} {opening.tag or '无编号'} {storey.number}F"
        try:
            leaf = place * bd.Box(panel_w * scale, thickness, panel_h * scale)
            leaf.label = f"{name} 扇"
            panels.append(leaf)
            # A frame is the opening minus its inset: one ring per opening, not
            # four bars, so the solid count stays close to one per component.
            frame = place * (
                bd.Box(opening.width * scale, 120.0, opening.height * scale)
                - bd.Box(panel_w * scale, 200.0, panel_h * scale)
            )
            frame.label = f"{name} 框"
            panels.append(frame)
        except Exception:  # noqa: BLE001
            continue
    return panels


def build_building(
    plan: BuildingPlan,
    out_path: str,
    *,
    scale_to_mm: float = 1.0,
    openings: list[Any] | None = None,
    segments: list[Any] | None = None,
    stairs: list[Any] | None = None,
) -> dict[str, Any]:
    """Extrude walls and slabs for every storey and write one STEP."""
    from .massing import _kernel, _solid_from_polygon, _to_mm, _bbox
    from .stairs import align_wells, slab_with_wells, stair_pieces, stairs_in
    from shapely.affinity import translate

    if not plan.storeys:
        raise ValueError("没有可装配的楼层。")

    # Plans are drawn side by side on the sheet, at different positions, so
    # every storey has to be moved onto the first one. Axis labels give the
    # exact offset; the bounding-box centre is the fallback.
    offsets, sources, align_scores = storey_offsets(plan)
    plan.notes.append(
        "楼层归位：" + "、".join(f"{number}层{sources[number]}" for number in sorted(sources))
    )
    aligned: dict[int, list[Polygon]] = {}
    for storey in plan.storeys:
        dx, dy = offsets[storey.number]
        aligned[storey.number] = [
            _to_mm(translate(polygon, xoff=dx, yoff=dy), scale_to_mm) for polygon in storey.walls
        ]

    bd = _kernel()
    solids = []
    failed = 0
    level_report = []

    for storey in plan.storeys:
        walls = aligned[storey.number]
        dx, dy = offsets[storey.number]
        storey_shapes = []
        for polygon in walls:
            try:
                solid = _solid_from_polygon(bd, polygon, storey.height)
            except Exception:  # noqa: BLE001
                failed += 1
                continue
            if solid is None or getattr(solid, "volume", 0) <= 0:
                failed += 1
                continue
            body = bd.Pos(0, 0, storey.base) * solid
            body.label = f"墙 {storey.number}F-{len(storey_shapes) + 1}"
            storey_shapes.append(body)

        cut = _cut_openings(bd, storey_shapes, storey, openings, segments, dx, dy, scale_to_mm)
        solids.extend(storey_shapes)
        panels = _panel_solids(bd, storey, openings, segments, dx, dy, scale_to_mm)
        solids.extend(panels)

        wells = storey.stairs or (stairs_in(stairs, storey.bounds) if stairs else [])
        wells = align_wells(wells, dx, dy, scale_to_mm)

        plates = slab_polygons(walls)
        slab_area = 0.0
        wells_cut = 0
        for plate in plates:
            hit = [well for well in wells if well.polygon().intersects(plate)]
            punched = slab_with_wells(plate, wells) or plate
            if punched.area < plate.area - 1.0:
                wells_cut += len(hit)
            slab_solid = _solid_from_polygon(bd, punched, plan.slab_thickness)
            if slab_solid is not None and getattr(slab_solid, "volume", 0) > 0:
                plate_solid = bd.Pos(0, 0, storey.base - plan.slab_thickness) * slab_solid
                plate_solid.label = f"{storey.number}层楼板"
                solids.append(plate_solid)
                slab_area += punched.area

        # The flights themselves. A stair well alone reads as a hole; the
        # treads are what make it a staircase in a section or a walk-through.
        stair_pieces_count = 0
        for well in wells:
            for footprint, z0, z1 in stair_pieces(well, storey.height, storey.base):
                if z1 - z0 <= 1.0:
                    continue
                try:
                    step = _solid_from_polygon(bd, _to_mm(footprint, 1.0), z1 - z0)
                except Exception:  # noqa: BLE001
                    continue
                if step is not None and getattr(step, "volume", 0) > 0:
                    tread = bd.Pos(0, 0, z0) * step
                    tread.label = f"楼梯 {storey.number}F-{stair_pieces_count + 1}"
                    solids.append(tread)
                    stair_pieces_count += 1

        level_report.append(
            {
                "floor": storey.number,
                "title": storey.title,
                "base": storey.base,
                "height": storey.height,
                "wallSolids": len(storey_shapes),
                "slab": bool(plates),
                "slabArea": round(slab_area / 1e6, 1),
                "openingsCut": cut,
                "panels": len(panels),
                "stairsCut": wells_cut,
                "stairPieces": stair_pieces_count,
                "alignSource": sources[storey.number],
                "alignOffset": [round(value, 1) for value in offsets[storey.number]],
                "alignScore": align_scores[storey.number],
            }
        )

    top = plan.storeys[-1]
    roof_walls = aligned[top.number]
    roof_plates = slab_polygons(roof_walls)
    for roof in roof_plates:
        roof_solid = _solid_from_polygon(bd, roof, plan.roof_thickness)
        if roof_solid is not None and getattr(roof_solid, "volume", 0) > 0:
            solids.append(bd.Pos(0, 0, top.base + top.height) * roof_solid)

    # A parapet rides on the roof edge. Nothing on the plan dimensions it, so
    # the height is an assumption that the report states.
    parapet_solids = 0
    if plan.parapet_height > 0:
        for roof in roof_plates:
            ring = roof.buffer(-plan.parapet_thickness, join_style="mitre")
            if ring.is_empty:
                continue
            band = roof.difference(ring)
            band_solid = _solid_from_polygon(bd, band, plan.parapet_height)
            if band_solid is not None and getattr(band_solid, "volume", 0) > 0:
                solids.append(bd.Pos(0, 0, top.base + top.height) * band_solid)
                parapet_solids += 1
        if parapet_solids:
            plan.notes.append(
                f"屋顶女儿墙：厚 {plan.parapet_thickness:.0f}mm、高 {plan.parapet_height:.0f}mm"
                "（图纸未标注，按常规取值）"
            )

    compound = bd.Compound(children=solids)
    bd.export_step(compound, str(out_path))
    return {
        "path": str(out_path),
        "solids": len(solids),
        "failed": failed,
        "volume": round(sum(float(solid.volume) for solid in solids), 1),
        "bbox": _bbox(compound),
        "levels": level_report,
        "notes": plan.notes,
    }
