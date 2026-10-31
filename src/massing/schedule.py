"""The door and window schedule: the drawing's own bill of quantities.

A 建施图 carries a 门窗表 — a table of every opening type, its tag, its size and
how many of them the building has. The tag already encodes the size (C1516 is
1500 x 1600), so the table adds two things the symbols cannot: the official
size, and a count to check the extraction against.

Parsing is by geometry, not by object type. In a DXF from ZWCAD the table is
drawn as ordinary TEXT entities in a grid — one text per cell, positioned by
its baseline — so a row is "texts sharing a baseline" and a column is "where
the header put it". Every other CAD system exports tables the same way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .model import TextItem

_SIZE = re.compile(r"(\d{3,4})\s*[XxX×*]\s*(\d{3,4})")
_INT = re.compile(r"\d{1,4}")
_TAG = re.compile(r"(FM|GC|MD|MLC|C|M)\s*[乙甲丙]?\s*\d{2,3}\d{2}")
_TYPE_WORDS = {"门", "窗", "防火门", "百叶", "百叶窗", "飘窗", "幕墙", "门联窗", "洞口", "洞"}
# The header cell that identifies a schedule rather than an elevation detail:
# the elevation drawings list 门窗编号, the schedule lists 设计编号.
_HEADER_WORD = "设计编号"


@dataclass
class ScheduleRow:
    tag: str
    kind: str
    width: float
    height: float
    count: int | None
    type_label: str = ""
    atlas: str = ""
    page: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "kind": self.kind,
            "width": self.width,
            "height": self.height,
            "count": self.count,
            "type": self.type_label,
            "atlas": self.atlas,
            "page": self.page,
        }


@dataclass
class Schedule:
    rows: list[ScheduleRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def by_tag(self) -> dict[str, ScheduleRow]:
        return {row.tag: row for row in self.rows}

    def counts(self) -> dict[str, int]:
        return {row.tag: row.count for row in self.rows if row.count is not None}

    def match_width(self, width: float, *, tolerance: float = 0.02, threshold: float = 0.6):
        """The scheduled type most likely to be a ``width``-wide opening.

        Only one width is needed, because the tag already gave the size for
        every labelled opening. This is for the unlabelled ones: the wall is
        split at them and the symbol draws their width line, but nothing says
        how tall they are.

        The nearest scheduled width wins outright — a band of "everything
        within 8%" would let the 1800 windows vote on a 1900 hole and turn a
        clear answer into a tie. And when the widths still span several
        heights (1800 is a 1600 window and a 2400 door), nothing is returned,
        because a wrong hole is worse than a reported gap.
        """
        if not self.rows:
            return None
        nearest = min(abs(row.width - width) for row in self.rows)
        if nearest > max(width * tolerance, 20.0):
            return None
        matches = [row for row in self.rows if abs(abs(row.width - width) - nearest) <= 1.0]
        weights: dict[float, float] = {}
        best: dict[float, ScheduleRow] = {}
        for row in matches:
            weight = float(row.count or 1)
            weights[row.height] = weights.get(row.height, 0.0) + weight
            if row.height not in best or weight > float(best[row.height].count or 1):
                best[row.height] = row
        total = sum(weights.values())
        height, weight = max(weights.items(), key=lambda item: item[1])
        if total <= 0 or weight / total < threshold:
            return None
        return best[height]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": [row.to_dict() for row in self.rows],
            "notes": list(self.notes),
        }


def _baseline_rows(texts: Iterable[TextItem], tolerance: float = 120.0) -> list[list[TextItem]]:
    """Group texts into visual rows by baseline, each row left-to-right."""
    placed = [text for text in texts if text.position]
    placed.sort(key=lambda text: text.position[1])
    rows: list[list[TextItem]] = []
    current_y: float | None = None
    for text in placed:
        if current_y is None or abs(text.position[1] - current_y) > tolerance:
            rows.append([text])
            current_y = text.position[1]
        else:
            rows[-1].append(text)
    for row in rows:
        row.sort(key=lambda text: text.position[0])
    return rows


def _is_header(row: list[TextItem]) -> bool:
    return any(_HEADER_WORD in (text.text or "") for text in row)


def _column_bands(header: list[TextItem]) -> dict[str, tuple[float, float]] | None:
    """The x span of each column, measured from where the header put it.

    A table cell is just text on a baseline: nothing marks which column it
    belongs to, and a drawing routinely puts unrelated text on the same
    baseline. Slicing by the header's own x positions is what separates the
    tag from a dimension string three metres to its left.
    """
    x: dict[str, float] = {}
    for text in header:
        if not text.position:
            continue
        value = (text.text or "").strip()
        if value.startswith("类型"):
            x.setdefault("type", text.position[0])
        elif "设计编号" in value:
            x.setdefault("tag", text.position[0])
        elif "洞口尺寸" in value:
            x.setdefault("size", text.position[0])
        elif value.startswith("数量"):
            x.setdefault("count", text.position[0])
    if "tag" not in x or "size" not in x:
        return None
    tag_x, size_x = x["tag"], x["size"]
    count_x = x.get("count", size_x + 1400.0)
    type_x = x.get("type", tag_x - 750.0)
    return {
        "type": (type_x - 420.0, tag_x - 60.0),
        "tag": (tag_x - 120.0, size_x - 400.0),
        "size": (size_x - 200.0, count_x - 220.0),
        "count": (count_x - 220.0, count_x + 400.0),
    }


def _slice(row: list[TextItem], bands: dict[str, tuple[float, float]]) -> dict[str, str]:
    cells: dict[str, list[str]] = {name: [] for name in bands}
    for text in row:
        if not text.position:
            continue
        px = text.position[0]
        for name, (lo, hi) in bands.items():
            if lo <= px <= hi:
                cells[name].append((text.text or "").strip())
                break
    return {name: "".join(values) for name, values in cells.items()}


def _row_from(cells: dict[str, str]) -> ScheduleRow | None:
    """One data row of the schedule, or ``None`` if the row is not one.

    The tag may be split across cells by the drawing ("FM" / "乙" / "1021"),
    which is why the tag column is concatenated rather than read as one cell.
    """
    match = _SIZE.search(cells.get("size", ""))
    if not match:
        return None
    tag = cells.get("tag", "")
    if not _TAG.search(tag):
        return None
    type_label = ""
    for word in _TYPE_WORDS:
        if word in cells.get("type", ""):
            type_label = word
            break
    count = None
    digits = _INT.search(cells.get("count", ""))
    if digits:
        count = int(digits.group(0))
    return ScheduleRow(
        tag=tag,
        kind=_kind_of(tag),
        width=float(match.group(1)),
        height=float(match.group(2)),
        count=count,
        type_label=type_label,
    )


def _kind_of(tag: str) -> str:
    from .openings import parse_tag

    parsed = parse_tag(tag)
    return parsed[0] if parsed else "unknown"


def read_schedule(texts: list[TextItem]) -> Schedule:
    """Every 门窗表 in the drawing, as rows."""
    schedule = Schedule()
    rows = _baseline_rows(texts)
    seen: set[tuple[str, float]] = set()

    for index, row in enumerate(rows):
        if not _is_header(row):
            continue
        bands = _column_bands(row)
        if bands is None:
            continue
        previous_y = row[0].position[1]
        # Rows run downward from the header, which in DXF coordinates means
        # decreasing y — the rows earlier in the sorted list, not later.
        for below in reversed(rows[:index]):
            y = below[0].position[1]
            gap = previous_y - y
            if gap < 0 or gap > 900.0:
                break
            previous_y = y
            parsed = _row_from(_slice(below, bands))
            if parsed is None:
                continue
            key = (parsed.tag, parsed.width)
            if key in seen:
                continue
            seen.add(key)
            schedule.rows.append(parsed)

    if not schedule.rows:
        schedule.notes.append("没有找到门窗表（表头需含「设计编号」）。")
    return schedule


def tag_conflicts(schedule: Schedule) -> list[dict[str, Any]]:
    """Scheduled sizes that contradict the tag's own naming rule.

    The tag is the drawing's shorthand for its size (C1516 is 1500 x 1600), so
    a schedule row saying otherwise is an inconsistency in the drawing, not in
    the reading. Cutting still follows the tag — it is what the opening is
    named after — but the difference is worth showing.
    """
    from .openings import parse_tag

    conflicts = []
    for row in schedule.rows:
        parsed = parse_tag(row.tag)
        if parsed is None:
            continue
        _kind, width, height = parsed
        if abs(width - row.width) > 1.0 or abs(height - row.height) > 1.0:
            conflicts.append(
                {
                    "tag": row.tag,
                    "schedule": [row.width, row.height],
                    "tagSize": [width, height],
                }
            )
    return conflicts


def reconcile(schedule: Schedule, openings: Iterable[Any]) -> dict[str, Any]:
    """Compare the schedule's counts with the openings actually read.

    Counts are per drawing and the drawings may be split across files, so a
    difference is reported, never treated as an error.
    """
    found: dict[str, int] = {}
    for opening in openings:
        tag = getattr(opening, "tag", "") or ""
        if not tag:
            continue
        found[tag] = found.get(tag, 0) + 1

    table = schedule.counts()
    rows = []
    for tag in sorted(set(table) | set(found)):
        expected = table.get(tag)
        actual = found.get(tag, 0)
        rows.append(
            {
                "tag": tag,
                "schedule": expected,
                "read": actual,
                "delta": None if expected is None else actual - expected,
            }
        )
    return {
        "rows": rows,
        "scheduled": sum(value for value in table.values() if value),
        "read": sum(found.values()),
        "conflicts": tag_conflicts(schedule),
    }
