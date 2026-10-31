"""Read a drawing the way the textbook says to read it.

The 识图 rules this follows, in the order the textbook teaches them:

* name the sheet and its scale before anything else;
* find the grid — numbers across, letters down, I/O/Z never used;
* read the plan inside out: outline, then rooms, then walls, then openings;
* check the dimensions in three tiers — overall, axis spacing, detail;
* collect the levels and the difference between inside and outside.

Everything below turns one of those steps into something computed rather than
eyeballed, and says when it could not decide.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from .axes import AxisLabel

_SCALE = re.compile(r"1\s*[:：]\s*(\d{2,4})")
_SHEET_TITLES = (
    ("总平面", "总平面图"),
    ("立面", "立面图"),
    ("剖面", "剖面图"),
    ("平面", "平面图"),
    ("大样", "详图"),
    ("详图", "详图"),
    ("节点", "详图"),
)
_ROOM_WORDS = (
    "教室", "办公室", "会议室", "实验室", "宿舍", "走廊", "门厅", "大厅", "楼梯间",
    "卫生间", "厕所", "盥洗", "茶水", "储藏", "库房", "配电", "值班", "电梯", "机房",
    "阳台", "平台", "休息", "阅览", "报告", "准备", "器材", "楼梯",
)
# A room smaller than this is a shaft or a gap between walls, not a room.
MIN_ROOM_AREA = 2.0


@dataclass
class Room:
    name: str
    area: float
    center: tuple[float, float]
    polygon: Any = None
    labeled_by: str = ""  # "文字" when a text sat inside, "推测" otherwise

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "area": round(self.area, 1),
            "center": [round(self.center[0], 1), round(self.center[1], 1)],
            "labeledBy": self.labeled_by,
        }


@dataclass
class Grid:
    columns: list[float] = field(default_factory=list)  # x of the numbered axes
    rows: list[float] = field(default_factory=list)     # y of the lettered axes
    column_labels: list[str] = field(default_factory=list)
    row_labels: list[str] = field(default_factory=list)

    @property
    def column_spacing(self) -> list[float]:
        return [round(b - a, 1) for a, b in zip(self.columns, self.columns[1:])]

    @property
    def row_spacing(self) -> list[float]:
        return [round(b - a, 1) for a, b in zip(self.rows, self.rows[1:])]

    def to_dict(self) -> dict[str, Any]:
        return {
            "columns": [round(value, 1) for value in self.columns],
            "rows": [round(value, 1) for value in self.rows],
            "columnLabels": self.column_labels,
            "rowLabels": self.row_labels,
            "columnSpacing": self.column_spacing,
            "rowSpacing": self.row_spacing,
        }


@dataclass
class Recognition:
    title: str = ""
    scale: str = ""
    sheet_kinds: list[str] = field(default_factory=list)
    grid: Grid = field(default_factory=Grid)
    rooms: list[Room] = field(default_factory=list)
    wall_count: int = 0
    column_count: int = 0
    wall_area: float = 0.0
    thickness: dict[str, int] = field(default_factory=dict)
    external_walls: int = 0
    internal_walls: int = 0
    openings: list[dict[str, Any]] = field(default_factory=list)
    opening_summary: dict[str, Any] = field(default_factory=dict)
    stairs: int = 0
    stair_size: str = ""
    elevations: list[float] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "scale": self.scale,
            "sheetKinds": list(self.sheet_kinds),
            "grid": self.grid.to_dict(),
            "rooms": [room.to_dict() for room in self.rooms],
            "walls": {
                "count": self.wall_count,
                "columns": self.column_count,
                "area": round(self.wall_area, 1),
                "thickness": self.thickness,
                "external": self.external_walls,
                "internal": self.internal_walls,
            },
            "openings": self.opening_summary,
            "openingRows": self.openings,
            "stairs": {"count": self.stairs, "size": self.stair_size},
            "elevations": self.elevations,
            "checks": list(self.checks),
            "notes": list(self.notes),
        }


# --- sheet name and scale --------------------------------------------------


def sheet(title_texts: list[str]) -> tuple[str, str, list[str]]:
    """The sheet's name, its scale, and every kind of drawing it holds."""
    kinds: list[str] = []
    title = ""
    for value in title_texts:
        for keyword, kind in _SHEET_TITLES:
            if keyword in value:
                if kind not in kinds:
                    kinds.append(kind)
                if kind in {"平面图", "总平面图", "立面图", "剖面图"} and len(value) <= 14:
                    if not title or len(value) < len(title):
                        title = value.strip()
                break
    scale = ""
    for value in title_texts:
        match = _SCALE.search(value)
        if match:
            scale = f"1:{match.group(1)}"
            break
    return title, scale, kinds


# --- grid ------------------------------------------------------------------


def grid_from_labels(labels: list[AxisLabel]) -> Grid:
    """Split the axis bubbles into numbered columns and lettered rows.

    The textbook's rule is the classifier: digits run left to right across the
    plan, letters run bottom to top, and I/O/Z are never used (they read as 1/0/2).
    """
    grid = Grid()
    xs: dict[str, float] = {}
    ys: dict[str, float] = {}
    for label in labels:
        text = label.text.strip()
        if not text:
            continue
        if text.isdigit():
            xs.setdefault(text, label.x)
        elif text.isalpha() and all(ch not in "IOZ" for ch in text.upper()):
            ys.setdefault(text, label.y)
    columns = sorted(xs.items(), key=lambda item: item[1])
    rows = sorted(ys.items(), key=lambda item: item[1])
    grid.column_labels = [name for name, _x in columns]
    grid.columns = [value for _name, value in columns]
    grid.row_labels = [name for name, _y in rows]
    grid.rows = [value for _name, value in rows]
    return grid


# --- rooms -----------------------------------------------------------------


def find_rooms(walls: list[Polygon], texts: list[Any], *, close_mm: float = 1500.0) -> list[Room]:
    """The enclosed spaces between the walls, each with its name.

    Walls do not meet at their junctions, so the network is not closed and a
    plain polygonize finds nothing. Closing the union by ``close_mm`` joins it
    into a shell; the shell's interior rings are then the rooms, which is the
    same operation that gives the floor plate, read the other way round.
    """
    if not walls:
        return []
    merged = unary_union(walls)
    closed = merged.buffer(close_mm, join_style="mitre").buffer(-close_mm, join_style="mitre")
    if closed.is_empty:
        return []
    parts = [part for part in getattr(closed, "geoms", [closed]) if isinstance(part, Polygon)]

    rooms: list[Room] = []
    for part in parts:
        for ring in part.interiors:
            polygon = Polygon(ring)
            if polygon.is_empty or polygon.area / 1e6 < MIN_ROOM_AREA:
                continue
            centre = (polygon.centroid.x, polygon.centroid.y)
            name, source = _room_name(polygon, texts)
            rooms.append(
                Room(
                    name=name,
                    area=polygon.area / 1e6,
                    center=centre,
                    polygon=polygon,
                    labeled_by=source,
                )
            )
    rooms.sort(key=lambda room: -room.area)
    return rooms


def _room_name(polygon: Polygon, texts: list[Any]) -> tuple[str, str]:
    """The text sitting inside the room, or a description of what is there."""
    from .openings import parse_tag

    inside = [
        text
        for text in texts
        if getattr(text, "position", None) and polygon.contains(Point(text.position))
    ]
    # A room holds more than its name: door tags, the stair's 上/下 arrows, level
    # labels and dimension figures all sit on the floor too. None of them name a
    # room, and taking one gives every room the same wrong name. Names are also
    # frequently exploded into one text per character, so a name is a whole
    # baseline, not a single entity.
    baselines: dict[int, list[Any]] = {}
    for text in inside:
        key = int(round(text.position[1] / 250.0))
        baselines.setdefault(key, []).append(text)

    candidates: list[str] = []
    for group in baselines.values():
        value = "".join(
            (text.text or "").strip() for text in sorted(group, key=lambda item: item.position[0])
        )
        if not value or len(value) > 12:
            continue
        # A stair's 上/下 arrows sit on the floor like a label does.
        if all(ch in "上下· " for ch in value):
            continue
        if parse_tag(value) is not None:
            continue
        if value.isdigit() or _SCALE.search(value):
            continue
        if re.fullmatch(r"[±+\-]?\d+(\.\d+)?", value):
            continue
        candidates.append(value)
    for value in candidates:
        if any(word in value for word in _ROOM_WORDS):
            return value, "文字"
    for value in candidates:
        if not value.isascii():
            return value, "文字"
    return "", "推测"


# --- walls -----------------------------------------------------------------


def shape_of(polygon: Polygon) -> tuple[float, float]:
    """``(thickness, length)`` from the minimum rectangle around the polygon.

    An axis-aligned bounding box would call a 45-degree wall far thicker than
    it is; the rotated box gives the wall's own thickness whatever its angle.
    """
    try:
        rect = polygon.minimum_rotated_rectangle
        points = list(rect.exterior.coords)[:4]
    except Exception:  # noqa: BLE001
        x0, y0, x1, y1 = polygon.bounds
        return min(x1 - x0, y1 - y0), max(x1 - x0, y1 - y0)
    if len(points) < 4:
        x0, y0, x1, y1 = polygon.bounds
        return min(x1 - x0, y1 - y0), max(x1 - x0, y1 - y0)
    first = math.dist(points[0], points[1])
    second = math.dist(points[1], points[2])
    return min(first, second), max(first, second)


def split_walls(
    polygons: list[Polygon],
    *,
    ratio: float = 2.5,
    min_thickness: float = 60.0,
    max_thickness: float = 600.0,
) -> tuple[list[Polygon], list[Polygon]]:
    """Walls and everything else travel in one list; only some of it is a wall.

    A wall is long against its thickness (at least 2.5 to 1) and between 60 and
    600 mm thick — the same range the extractor accepts. A column is squatter
    than that; a merged blob of several walls is thicker than that. Counting
    either as a wall inflates the wall count and pollutes the thickness table.
    """
    walls: list[Polygon] = []
    others: list[Polygon] = []
    for polygon in polygons:
        thickness, length = shape_of(polygon)
        if (
            thickness >= min_thickness
            and thickness <= max_thickness
            and length / thickness >= ratio
        ):
            walls.append(polygon)
        else:
            others.append(polygon)
    return walls, others


def classify_walls(
    walls: list[Polygon],
    *,
    network: list[Polygon] | None = None,
    close_mm: float = 1500.0,
    edge_mm: float = 600.0,
) -> tuple[int, int]:
    """``(external, internal)`` counts, by distance to the building outline.

    The outline is the exterior ring of the closed wall network — the same ring
    the floor plate is drawn from. Using the network's own boundary would call
    every wall external, because every wall touches some edge of the union.
    ``network`` is the full set of plan geometry; the filter that separates
    walls from columns leaves holes in the ring if it is used here.
    """
    if not walls:
        return 0, 0
    merged = unary_union(network if network else walls)
    shell = merged.buffer(close_mm, join_style="mitre").buffer(-close_mm, join_style="mitre")
    if shell.is_empty:
        return 0, len(walls)
    parts = [part for part in getattr(shell, "geoms", [shell]) if isinstance(part, Polygon)]
    if not parts:
        return 0, len(walls)
    outline = LineString(max(parts, key=lambda part: part.area).exterior.coords)
    external = 0
    for polygon in walls:
        try:
            if outline.distance(polygon.centroid) <= edge_mm:
                external += 1
        except Exception:  # noqa: BLE001
            continue
    return external, len(walls) - external


# --- openings --------------------------------------------------------------


def summarise_openings(openings: list[Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Group the openings by tag, the way a 门窗表 does."""
    groups: dict[str, dict[str, Any]] = {}
    for opening in openings:
        tag = getattr(opening, "tag", "") or "无编号"
        entry = groups.setdefault(
            tag,
            {
                "tag": tag,
                "kind": getattr(opening, "kind", ""),
                "width": round(getattr(opening, "width", 0.0), 1),
                "height": round(getattr(opening, "height", 0.0), 1),
                "count": 0,
                "inferred": 0,
            },
        )
        entry["count"] += 1
        if getattr(opening, "inferred", False):
            entry["inferred"] += 1
    rows = sorted(groups.values(), key=lambda item: (-item["count"], item["tag"]))
    kinds = Counter(row["kind"] for row in rows for _ in range(row["count"]))
    summary = {
        "total": sum(row["count"] for row in rows),
        "kinds": dict(kinds),
        "types": len(rows),
    }
    return rows, summary


# --- dimensions ------------------------------------------------------------


def dimension_tiers(dimensions: list[Any], bounds: tuple[float, float, float, float]) -> dict[str, list[float]]:
    """Split the dimension strings into the textbook's three tiers.

    Overall, axis, detail: measured by how far the dimension line sits from the
    building, which is exactly how they are laid out on the sheet.
    """
    x0, y0, x1, y1 = bounds
    outside: list[tuple[float, float]] = []
    for dim in dimensions:
        if not dim.position or dim.measurement is None:
            continue
        px, py = dim.position
        distance = min(abs(px - x0), abs(px - x1), abs(py - y0), abs(py - y1))
        if distance > 0:
            outside.append((distance, float(dim.measurement)))
    if not outside:
        return {}
    outside.sort()
    # Three bands by rank: the nearest third are the detail strings, the
    # farthest third the overall ones. Splitting on distance values instead
    # collapses all three into one band whenever the strings are evenly spaced.
    count = len(outside)
    step = max(1, count // 3)
    bands = {
        "细部": outside[:step],
        "轴线": outside[step : 2 * step],
        "总": outside[2 * step :],
    }
    return {name: sorted({round(value, 1) for _distance, value in band})[-8:] for name, band in bands.items() if band}


# --- the whole reading -----------------------------------------------------


def recognize(
    path: str,
    *,
    walls: list[Polygon],
    texts: list[Any],
    openings: list[Any],
    stairs: list[Any],
    labels: list[AxisLabel],
    dimensions: list[Any],
    elevations: list[float],
    bounds: tuple[float, float, float, float],
) -> Recognition:
    result = Recognition()
    titles = [str(getattr(text, "text", "") or "").strip() for text in texts]
    result.title, result.scale, result.sheet_kinds = sheet(titles)
    result.grid = grid_from_labels(labels)

    wall_parts, column_parts = split_walls(walls)
    # Rooms are closed by everything on the plan, short piers included: the
    # filter that separates walls from columns is for the counts, and applying
    # it here breaks the ring and loses every room.
    result.rooms = find_rooms(walls, texts)
    result.wall_count = len(wall_parts)
    result.column_count = len(column_parts)
    result.wall_area = sum(polygon.area for polygon in wall_parts)
    result.thickness = dict(
        Counter(int(round(shape_of(polygon)[0] / 10) * 10) for polygon in wall_parts).most_common()
    )
    result.external_walls, result.internal_walls = classify_walls(wall_parts, network=walls)

    result.openings, result.opening_summary = summarise_openings(openings)
    result.stairs = len(stairs)
    if stairs:
        sizes = Counter(f"{round(well.bounds[2] - well.bounds[0])}×{round(well.bounds[3] - well.bounds[1])}" for well in stairs)
        result.stair_size = sizes.most_common(1)[0][0]
    result.elevations = elevations

    tiers = dimension_tiers(dimensions, bounds)
    if tiers.get("总"):
        result.notes.append("总尺寸标注：" + "、".join(f"{value:.0f}" for value in tiers["总"][-3:]))
    if tiers.get("轴线"):
        result.notes.append("轴线尺寸：" + "、".join(f"{value:.0f}" for value in tiers["轴线"][-6:]))

    if not result.rooms:
        result.checks.append("没有识别出房间：墙体可能没有围合，或图纸是立面/剖面/详图而不是平面图。")
    if not result.grid.columns and not result.grid.rows:
        result.checks.append("没有读到轴号：图里可能没有定位轴线，或轴号不是 SWR_AXIS_LABEL 对象。")
    if result.sheet_kinds and "平面图" not in result.sheet_kinds:
        result.checks.append("这张图不是平面图（" + "、".join(result.sheet_kinds) + "），房间与墙体统计仅供参考。")
    return result
