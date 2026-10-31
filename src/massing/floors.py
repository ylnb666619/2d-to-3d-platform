"""Group extracted wall areas into floor plans.

A 建施图 usually lays every storey side by side in model space. Before the
massing can be stacked, those groups have to be found, and the finding has to
be visible: the report says how big each group is and where, so a mis-grouped
detail can be spotted and excluded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from shapely.geometry import Polygon


@dataclass
class FloorGroup:
    index: int
    bounds: tuple[float, float, float, float]
    polygons: list[Polygon] = field(default_factory=list)

    @property
    def area(self) -> float:
        return sum(polygon.area for polygon in self.polygons)

    @property
    def centroid_y(self) -> float:
        return (self.bounds[1] + self.bounds[3]) / 2

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "bounds": [round(value, 1) for value in self.bounds],
            "polygons": len(self.polygons),
            "area": round(self.area, 1),
        }


def _union(parent: list[int], a: int, b: int) -> None:
    ra, rb = _find(parent, a), _find(parent, b)
    if ra != rb:
        parent[rb] = ra


def _find(parent: list[int], node: int) -> int:
    while parent[node] != node:
        parent[node] = parent[parent[node]]
        node = parent[node]
    return node


def group_floors(
    polygons: Iterable[Polygon],
    *,
    gap_mm: float = 20_000.0,
    min_polygons: int = 10,
) -> list[FloorGroup]:
    """Split wall areas into spatially separate groups, largest first.

    Two areas belong to the same drawing when their bounding boxes are within
    ``gap_mm`` of each other. Storeys laid out on one sheet are metres apart;
    a detail sheet is usually much further away, or far smaller, so ``min_polygons``
    drops it from the stack while the report still shows it.
    """
    items = list(polygons)
    if not items:
        return []

    boxes = [polygon.bounds for polygon in items]
    parent = list(range(len(items)))

    expanded = [
        (x0 - gap_mm, y0 - gap_mm, x1 + gap_mm, y1 + gap_mm) for (x0, y0, x1, y1) in boxes
    ]
    for i in range(len(items)):
        ax0, ay0, ax1, ay1 = expanded[i]
        for j in range(i + 1, len(items)):
            bx0, by0, bx1, by1 = expanded[j]
            if ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0:
                continue
            _union(parent, i, j)

    members: dict[int, list[Polygon]] = {}
    for index, polygon in enumerate(items):
        members.setdefault(_find(parent, index), []).append(polygon)

    groups: list[FloorGroup] = []
    for polygons_in_group in members.values():
        if len(polygons_in_group) < min_polygons:
            continue
        xs0 = min(p.bounds[0] for p in polygons_in_group)
        ys0 = min(p.bounds[1] for p in polygons_in_group)
        xs1 = max(p.bounds[2] for p in polygons_in_group)
        ys1 = max(p.bounds[3] for p in polygons_in_group)
        groups.append(FloorGroup(index=0, bounds=(xs0, ys0, xs1, ys1), polygons=polygons_in_group))

    groups.sort(key=lambda group: -group.area)
    for index, group in enumerate(groups, start=1):
        group.index = index
    return groups


def order_bottom_up(groups: list[FloorGroup]) -> list[FloorGroup]:
    """Order groups as storeys: the one lowest on the sheet is the ground floor.

    That is the convention in a 建施图 — plans are stacked with the ground floor
    at the bottom. ``--floor-order y-desc`` flips it for drawings that do the
    opposite.
    """
    return sorted(groups, key=lambda group: group.centroid_y)
