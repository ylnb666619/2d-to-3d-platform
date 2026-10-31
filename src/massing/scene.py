"""The assembled building as prisms a browser can draw.

The STEP export goes through a CAD kernel: exact B-rep, minutes to build, and
nothing but a CAD application can open it. The same building also has a much
humbler description -- footprint rings, a bottom, a top -- and that is all a
browser needs to draw it at 60 fps.

Nothing here is a solid. Two prisms that overlap are two prisms, and a door is
a coloured box standing in the wall rather than a hole cut through it. That is
the price of skipping the kernel, and it is the right price for a view: the
kernel still owns the file that goes to Revit.

Output is in metres. The drawing is in millimetres; a browser camera, a ray
cast and a float32 vertex all behave better at building scale in metres.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

from shapely.affinity import scale as affinity_scale
from shapely.affinity import translate
from shapely.geometry import Polygon
from shapely.ops import unary_union

from .building import BuildingPlan, slab_polygons, storey_offsets

# Order matters: the page lists the layers in this order.
CATEGORIES = ("wall", "column", "slab", "stair", "door", "window", "room", "roof", "parapet")

_LABEL = {
    "wall": "墙",
    "column": "柱",
    "slab": "楼板",
    "stair": "楼梯",
    "door": "门",
    "window": "窗",
    "room": "房间",
    "roof": "屋面板",
    "parapet": "女儿墙",
}


def _parts(geometry: Any) -> list[Polygon]:
    """Every polygon in a shapely result, whatever it is wrapped in."""
    if geometry is None or getattr(geometry, "is_empty", True):
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    parts: list[Polygon] = []
    for part in getattr(geometry, "geoms", ()):
        parts.extend(_parts(part))
    return parts


def _rings(polygon: Polygon) -> list[list[list[float]]]:
    """Outer ring then holes, each without its closing point.

    The browser triangulates these, and a repeated last point is a degenerate
    triangle waiting to happen.
    """
    rings = [list(polygon.exterior.coords)]
    rings.extend(list(interior.coords) for interior in polygon.interiors)
    return [ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring for ring in rings]


def _place(geometry: Any, dx: float, dy: float, scale: float) -> list[Polygon]:
    """Move a polygon onto the storey's common frame and into millimetres.

    The offset is applied here and nowhere else: it is the one transform that,
    applied twice, silently tears the building into four separate blocks.
    """
    moved = translate(geometry, xoff=dx, yoff=dy) if (dx or dy) else geometry
    if scale != 1.0:
        moved = affinity_scale(moved, xfact=scale, yfact=scale, origin=(0, 0))
    return _parts(moved)


def _layer_of(polygon: Polygon, segments: Iterable[Any]) -> str:
    """The CAD layer a wall came from, by nearest centreline.

    The polygons and the centrelines are produced by the same pass, so the
    nearest one is the one it came from. Naming a wall after its layer is what
    an architect expects to see; ``墙 2F-17`` is a number nobody drew. A layer
    called ``0`` is the absence of a decision, not a name.
    """
    centre = polygon.representative_point()
    best: tuple[float, str] | None = None
    for segment in segments:
        layer = getattr(segment, "layer", "") or ""
        if not layer or layer in {"0", "Defpoints"}:
            continue
        midpoint = getattr(segment, "midpoint", None)
        if midpoint is None:
            continue
        distance = (midpoint[0] - centre.x) ** 2 + (midpoint[1] - centre.y) ** 2
        if best is None or distance < best[0]:
            best = (distance, layer)
    return best[1] if best else ""


def _box(cx: float, cy: float, angle: float, width: float, depth: float) -> Polygon:
    """A rectangle of ``width`` along ``angle`` and ``depth`` across it."""
    ux, uy = math.cos(angle), math.sin(angle)
    nx, ny = -uy, ux
    half_w, half_d = width / 2.0, depth / 2.0
    return Polygon(
        [
            (cx - ux * half_w - nx * half_d, cy - uy * half_w - ny * half_d),
            (cx + ux * half_w - nx * half_d, cy + uy * half_w - ny * half_d),
            (cx + ux * half_w + nx * half_d, cy + uy * half_w + ny * half_d),
            (cx - ux * half_w + nx * half_d, cy - uy * half_w + ny * half_d),
        ]
    )


class _Collector:
    """Items in the storey's common millimetre frame, in metres at the end.

    Everything handed to ``add`` is already moved onto the first storey and
    scaled — the offset is applied exactly once, where the geometry is placed,
    not again here. Applying it twice is the classic way to lose a building.
    """

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self.counts: dict[str, int] = {}
        self.skipped = 0
        self._next: dict[tuple[int, str], int] = {}

    def add(
        self,
        category: str,
        storey_number: int,
        geometry: Any,
        z0: float,
        z1: float,
        *,
        name: str = "",
        layer: str = "",
        extra: dict[str, Any] | None = None,
    ) -> int:
        if z1 - z0 <= 0.5:
            return 0
        added = 0
        for part in _parts(geometry):
            if part.area <= 1.0 or len(part.exterior.coords) < 4:
                continue
            key = (storey_number, category)
            self._next[key] = self._next.get(key, 0) + 1
            index = self._next[key]
            label = name or f"{layer or _LABEL.get(category, category)} {storey_number}F-{index}"
            item = {
                "c": category,
                "f": storey_number,
                "n": label,
                "r": [[[x, y] for x, y in ring] for ring in _rings(part)],
                "z": [round(z0, 1), round(z1, 1)],
                "a": round(part.area / 1e6, 2),
                "v": round(part.area * (z1 - z0) / 1e9, 3),
            }
            if extra:
                item.update(extra)
            self.items.append(item)
            self.counts[category] = self.counts.get(category, 0) + 1
            added += 1
        return added


def build_scene(
    plan: BuildingPlan,
    *,
    openings: list[Any] | None = None,
    segments: list[Any] | None = None,
    stairs: list[Any] | None = None,
    scale_to_mm: float = 1.0,
    opening_thickness: float = 300.0,
    room_plate: float = 8.0,
) -> dict[str, Any]:
    """Every component of every storey, as a prism, in metres.

    The storeys are moved onto the first one with the same four datums and the
    same scoring as the STEP build, so the view and the file agree; a model
    that is drawn in one place and exported from another is worse than no view
    at all.
    """
    from .openings import openings_in, place_openings
    from .stairs import align_wells, stair_pieces, stairs_in

    if not plan.storeys:
        raise ValueError("没有可显示的楼层。")

    offsets, align_sources, align_scores = storey_offsets(plan)
    collector = _Collector()
    floors: list[dict[str, Any]] = []

    for storey in plan.storeys:
        dx, dy = offsets[storey.number]
        number = storey.number
        base, height = storey.base, storey.height

        columns = [
            part for polygon in storey.columns for part in _place(polygon, dx, dy, scale_to_mm)
        ]
        walls = [part for polygon in storey.walls for part in _place(polygon, dx, dy, scale_to_mm)]

        storey_segments = storey.segments or [
            segment
            for segment in (segments or [])
            if storey.bounds[0] - 500 <= segment.midpoint[0] <= storey.bounds[2] + 500
            and storey.bounds[1] - 500 <= segment.midpoint[1] <= storey.bounds[3] + 500
        ]

        # The wall polygons already contain the column footprints -- both come
        # out of the same proxy pass -- so the columns are cut out of the walls
        # and drawn on their own. A column that is also a wall cannot be picked.
        # The layer is looked up before the move: the segments have not moved.
        column_union = unary_union(columns) if columns else None
        for polygon in storey.walls:
            layer = _layer_of(polygon, storey_segments)
            for part in _place(polygon, dx, dy, scale_to_mm):
                shapes = [part]
                if column_union is not None:
                    try:
                        shapes = _parts(part.difference(column_union))
                    except Exception:  # noqa: BLE001 - a broken cut keeps the wall
                        shapes = [part]
                for shape in shapes:
                    collector.add("wall", number, shape, base, base + height, layer=layer)

        for polygon in columns:
            collector.add("column", number, polygon, base, base + height)

        wells = storey.stairs or (stairs_in(stairs, storey.bounds) if stairs else [])
        wells = align_wells(wells, dx, dy, scale_to_mm)

        for plate in slab_polygons(walls):
            punched = plate
            for well in wells:
                try:
                    punched = punched.difference(well.polygon())
                except Exception:  # noqa: BLE001
                    continue
            collector.add("slab", number, punched, base - plan.slab_thickness, base)

        for well in wells:
            pieces = stair_pieces(well, height, base)
            for index, (footprint, z0, z1) in enumerate(pieces, start=1):
                collector.add(
                    "stair", number, footprint, z0, z1,
                    name=f"楼梯 {number}F-{index}",
                )

        source_openings = storey.openings or openings_in(openings or [], storey.bounds)
        if storey_segments:
            placed = place_openings(source_openings, storey_segments)
        else:
            placed = []
        for opening, _wall, centre, angle in placed:
            z0 = base + opening.sill
            z1 = base + min(opening.sill + opening.height, height)
            if z1 - z0 < 100.0:
                continue
            rectangle = _box(
                (centre[0] + dx) * scale_to_mm,
                (centre[1] + dy) * scale_to_mm,
                angle,
                opening.width * scale_to_mm,
                opening_thickness * scale_to_mm,
            )
            category = "door" if opening.kind in {"door", "fire_door"} else "window"
            tag = opening.tag or "无编号"
            collector.add(
                category, number, rectangle, z0, z1,
                name=f"{_LABEL[category]} {tag} {number}F",
                extra={"tag": opening.tag, "kind": opening.kind, "sill": round(opening.sill, 0)},
            )

        # Room plates sit a few millimetres above the slab so they do not
        # fight it for the same depth value.
        for room in storey.rooms or []:
            polygon = getattr(room, "polygon", None)
            if polygon is None:
                continue
            name = getattr(room, "name", "") or "未命名房间"
            collector.add(
                "room", number, _place(polygon, dx, dy, scale_to_mm),
                base + 2.0, base + 2.0 + room_plate,
                name=f"{name}（{number}F）",
            )

        floors.append(
            {
                "n": number,
                "z": round(base, 1),
                "h": round(height, 1),
                "title": storey.title,
                "align": align_sources.get(number, ""),
                "score": align_scores.get(number, 0.0),
                "walls": len(storey.walls),
                "columns": len(storey.columns),
                "openings": len(placed),
                "stairs": len(wells),
                "rooms": len(storey.rooms or []),
            }
        )

    # Roof and parapet ride on the top storey, exactly as the STEP build has it.
    top = plan.storeys[-1]
    dx, dy = offsets[top.number]
    top_walls = [part for polygon in top.walls for part in _place(polygon, dx, dy, scale_to_mm)]
    roof_z = top.base + top.height
    for plate in slab_polygons(top_walls):
        collector.add("roof", top.number, plate, roof_z, roof_z + plan.roof_thickness)
        if plan.parapet_height > 0:
            ring = plate.buffer(-plan.parapet_thickness, join_style="mitre")
            if ring.is_empty:
                continue
            collector.add(
                "parapet", top.number, plate.difference(ring),
                roof_z, roof_z + plan.parapet_height,
            )

    return _normalise(collector, floors, plan)


def _normalise(collector: _Collector, floors: list[dict[str, Any]], plan: BuildingPlan) -> dict[str, Any]:
    """Shift to the origin, scale to metres, and round for the wire."""
    if not collector.items:
        raise ValueError("装配结果里没有任何构件。")

    xs: list[float] = []
    ys: list[float] = []
    for item in collector.items:
        for x, y in item["r"][0]:
            xs.append(x)
            ys.append(y)
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0

    for item in collector.items:
        item["r"] = [
            [[round((x - cx) / 1000.0, 3), round((y - cy) / 1000.0, 3)] for x, y in ring]
            for ring in item["r"]
        ]
        item["z"] = [round(value / 1000.0, 3) for value in item["z"]]

    for floor in floors:
        floor["z"] = round(floor["z"] / 1000.0, 3)
        floor["h"] = round(floor["h"] / 1000.0, 3)

    bounds = {
        "x": [round((min(xs) - cx) / 1000.0, 3), round((max(xs) - cx) / 1000.0, 3)],
        "y": [round((min(ys) - cy) / 1000.0, 3), round((max(ys) - cy) / 1000.0, 3)],
        "z": [
            round(min(item["z"][0] for item in collector.items), 3),
            round(max(item["z"][1] for item in collector.items), 3),
        ],
    }
    return {
        "unit": "m",
        "floors": floors,
        "items": collector.items,
        "counts": collector.counts,
        "categories": [category for category in CATEGORIES if category in collector.counts],
        "bounds": bounds,
        "notes": list(plan.notes),
    }
