"""One read of a drawing: the sheets it holds, and the building they describe.

The reading page and the 3D view both want the same facts, and getting them is
the slow part -- a 1.5 MB 建施图 takes tens of seconds to open, extract and
recognise. Reading it twice would double that wait and, worse, let the two
answers disagree. So this module reads once and hands back everything: the
storeys, the scene the browser draws, and one recognised plan per sheet.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .building import Storey, match_floors, plan_from_matches
from .document import iter_segments, open_document, read_document
from .floors import group_floors
from .massing import read_elevations, read_height
from .openings import openings_in, read_openings
from .recognize import find_rooms, recognize
from .schedule import read_schedule
from .stairs import read_stairs, stairs_in
from .walls import ExtractOptions, extract

PAD = 3000.0


@dataclass
class Reading:
    path: str
    views: list[Any] = field(default_factory=list)
    reports: list[Any] = field(default_factory=list)
    plan: Any = None
    scene: dict[str, Any] | None = None
    stats: dict[str, Any] = field(default_factory=dict)
    schedule_check: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ground: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "ground": self.ground,
            "plans": [report.to_dict() for report in self.reports],
            "scene": (
                {
                    "floors": self.scene["floors"],
                    "counts": self.scene["counts"],
                    "bounds": self.scene["bounds"],
                }
                if self.scene
                else None
            ),
            "stats": self.stats,
            "scheduleCheck": self.schedule_check,
            "warnings": self.warnings,
            "notes": self.notes,
        }


class _Steps:
    """A one-line progress channel for the browser, and a timing log."""

    def __init__(self, callback: Callable[[str, str], None] | None) -> None:
        self.callback = callback
        self.log: list[dict[str, Any]] = []
        self._start = time.perf_counter()

    def __call__(self, state: str, text: str) -> None:
        elapsed = round(time.perf_counter() - self._start, 1)
        self.log.append({"state": state, "text": text, "seconds": elapsed})
        if self.callback is not None:
            self.callback(state, text)


def _sheet_lines(lines: list[Any], bounds: tuple[float, float, float, float]) -> list[Any]:
    x0, y0, x1, y1 = bounds
    return [
        (ax, ay, bx, by)
        for ax, ay, bx, by in lines
        if x0 - PAD <= (ax + bx) / 2 <= x1 + PAD and y0 - PAD <= (ay + by) / 2 <= y1 + PAD
    ]


def _near_texts(texts: list[Any], bounds: tuple[float, float, float, float]) -> list[Any]:
    x0, y0, x1, y1 = bounds
    return [
        text
        for text in texts
        if not text.position
        or (x0 - PAD <= text.position[0] <= x1 + PAD and y0 - PAD <= text.position[1] <= y1 + PAD)
    ]


def _sheet(
    found: Any,
    *,
    title: str,
    walls: list[Any],
    bounds: tuple[float, float, float, float],
    openings: list[Any],
    stairs: list[Any],
    labels: list[Any],
    lines: list[Any],
) -> Any:
    from .webui import PlanView

    if title and not found.title:
        found.title = title
    return PlanView(
        title=found.title or title,
        bounds=bounds,
        walls=walls,
        rooms=found.rooms,
        openings=openings_in(openings, bounds),
        stairs=stairs_in(stairs, bounds),
        grid=found.grid,
        report=found,
        segments=_sheet_lines(lines, bounds),
    )


def _recognise(
    path: str,
    *,
    title: str,
    walls: list[Any],
    bounds: tuple[float, float, float, float],
    texts: list[Any],
    dimensions: list[Any],
    openings: list[Any],
    stairs: list[Any],
    labels: list[Any],
    lines: list[Any],
) -> Any:
    plan_openings = openings_in(openings, bounds)
    plan_stairs = stairs_in(stairs, bounds)
    from .axes import labels_in

    found = recognize(
        path,
        walls=walls,
        texts=_near_texts(texts, bounds),
        openings=plan_openings,
        stairs=plan_stairs,
        labels=labels_in(labels, bounds),
        dimensions=[
            dim
            for dim in dimensions
            if not dim.position
            or (
                bounds[0] - PAD <= dim.position[0] <= bounds[2] + PAD
                and bounds[1] - PAD <= dim.position[1] <= bounds[3] + PAD
            )
        ],
        elevations=read_elevations(_near_texts(texts, bounds)),
        bounds=bounds,
    )
    return _sheet(
        found,
        title=title,
        walls=walls,
        bounds=bounds,
        openings=openings,
        stairs=stairs,
        labels=labels,
        lines=lines,
    )


def _file_facts(path: str) -> dict[str, Any]:
    """Everything a drawing says about itself, in one open/close."""
    from .axes import read_axis_labels

    document, warnings = open_document(path)
    try:
        info = read_document(path, document=document, warnings=list(warnings), close=False)
        schedule = read_schedule(info.texts)
        openings = read_openings(document, schedule)
        stairs = read_stairs(document)
        labels = read_axis_labels(document)
        lines = [(a[0], a[1], b[0], b[1]) for a, b, _layer, _handle in iter_segments(document)]
    finally:
        try:
            document.close()
        except Exception:  # noqa: BLE001
            pass
    return {
        "info": info,
        "schedule": schedule,
        "openings": openings,
        "stairs": stairs,
        "labels": labels,
        "lines": lines,
        "warnings": list(warnings),
    }


def _apply_ground(
    plan: Any,
    result: Any,
    facts: dict[str, Any],
    *,
    default_height: float,
    path: str,
) -> tuple[Any, Any]:
    """Replace a borrowed ground floor with the one drawn in its own file."""
    groups = group_floors(result.polygons, gap_mm=8_000.0, min_polygons=10)
    if not groups or not plan.storeys:
        return plan, None
    group = max(groups, key=lambda item: item.area)
    height = plan.storeys[0].height or default_height
    x0, y0, x1, y1 = group.bounds
    rooms = find_rooms(list(group.polygons), _near_texts(facts["info"].texts, group.bounds))
    plan.storeys[0] = Storey(
        number=1,
        walls=list(group.polygons),
        base=0.0,
        height=height,
        title=f"一层（{Path(path).name}）",
        center=((x0 + x1) / 2, (y0 + y1) / 2),
        bounds=group.bounds,
        openings=facts["openings"],
        segments=result.segments,
        stairs=facts["stairs"],
        axes=facts["labels"],
        columns=result.columns,
        rooms=rooms,
    )
    plan.notes = [note for note in plan.notes if "第 1 层" not in note]
    plan.notes.append(f"首层取自 {Path(path).name}")
    return plan, group


def read_drawing(
    path: str,
    *,
    ground: str | None = None,
    config: str | None = None,
    group_gap: float = 8_000.0,
    min_floor_walls: int = 10,
    with_scene: bool = True,
    on_step: Callable[[str, str], None] | None = None,
) -> Reading:
    """Read a drawing once: storeys, scene, and one plan per sheet."""
    step = _Steps(on_step)
    reading = Reading(path=path, ground=ground or "")
    step("开始", f"打开 {Path(path).name}")

    facts = _file_facts(path)
    info = facts["info"]
    reading.warnings.extend(facts["warnings"])
    step("读图", f"图层 {len(info.layers)} 个，文字 {len(info.texts)} 条，门窗 {len(facts['openings'])} 个")

    result = extract(path, config_path=config, options=ExtractOptions())
    reading.stats = dict(result.stats)
    step("提墙", f"墙面 {len(result.polygons)} 个，墙段 {len(result.segments)} 条，柱 {len(result.columns)} 根")
    if not result.polygons:
        raise ValueError("没有提取到任何墙面。")

    # One sheet holds several plans side by side, and the plans are what the
    # rest of the pipeline needs; the gap is capped so neighbouring floors are
    # not merged into one drawing.
    groups = group_floors(
        result.polygons, gap_mm=min(group_gap, 8_000.0), min_polygons=min_floor_walls
    )
    matches = match_floors(info.texts, groups) if groups else []
    step("分层", f"{len(groups)} 个图幅：" + "、".join(match.title or "未命名" for match in matches))

    height = read_height(info.texts)
    plan = plan_from_matches(
        matches,
        height.elevations_mm or [],
        default_height=height.value_mm,
        openings=facts["openings"],
        segments=result.segments,
        stairs=facts["stairs"],
        axes=facts["labels"],
        columns=result.columns,
        texts=info.texts,
    )
    reading.plan = plan
    step("装配", f"{len(plan.storeys)} 层，层高 {height.value_mm:.0f}mm（{height.source}）")

    ground_group = None
    ground_facts = None
    if ground:
        ground_path = str(ground)
        ground_facts = _file_facts(ground_path)
        ground_result = extract(ground_path, config_path=config, options=ExtractOptions())
        plan, ground_group = _apply_ground(
            plan, ground_result, ground_facts, default_height=height.value_mm, path=ground_path
        )
        reading.ground = ground_path
        step("首层", f"{Path(ground_path).name}：墙面 {len(ground_result.polygons)} 个")

    if with_scene:
        from .scene import build_scene

        reading.scene = build_scene(
            plan,
            openings=facts["openings"],
            segments=result.segments,
            stairs=facts["stairs"],
        )
        counts = reading.scene["counts"]
        step(
            "生成模型",
            "、".join(f"{name} {count}" for name, count in counts.items()) or "无构件",
        )

    views: list[Any] = []
    reports: list[Any] = []
    if ground_group is not None and ground_facts is not None:
        view = _recognise(
            reading.ground,
            title="首层平面图",
            walls=ground_group.polygons,
            bounds=ground_group.bounds,
            texts=ground_facts["info"].texts,
            dimensions=ground_facts["info"].dimensions,
            openings=ground_facts["openings"],
            stairs=ground_facts["stairs"],
            labels=ground_facts["labels"],
            lines=ground_facts["lines"],
        )
        views.append(view)
        reports.append(view.report)

    if not matches:
        view = _recognise(
            path,
            title="",
            walls=result.polygons,
            bounds=info.bounds or (0.0, 0.0, 0.0, 0.0),
            texts=info.texts,
            dimensions=info.dimensions,
            openings=facts["openings"],
            stairs=facts["stairs"],
            labels=facts["labels"],
            lines=facts["lines"],
        )
        views.append(view)
        reports.append(view.report)
    else:
        for match in matches:
            view = _recognise(
                path,
                title=match.title,
                walls=match.group.polygons,
                bounds=match.group.bounds,
                texts=info.texts,
                dimensions=info.dimensions,
                openings=facts["openings"],
                stairs=facts["stairs"],
                labels=facts["labels"],
                lines=facts["lines"],
            )
            views.append(view)
            reports.append(view.report)
    step("识图", f"{len(reports)} 张平面")

    reading.views = views
    reading.reports = reports
    reading.notes = list(plan.notes)
    from .schedule import reconcile

    reading.schedule_check = reconcile(facts["schedule"], facts["openings"])
    return reading
