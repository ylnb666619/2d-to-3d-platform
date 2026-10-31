"""A local front end for the reading: the plan, the model, and the numbers.

Three panes: the plans and their layers on the left, the drawing -- or the
building, seen in three dimensions -- in the middle, the conclusions on the
right. It is one file with the data embedded, so the reading works from disk
with no server. The model tab draws the building itself with three.js from the
scene the pipeline emitted: no kernel, no STEP viewer, and every component
knows its own name when you click it.
"""

from __future__ import annotations

import html
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

COLOURS = {
    "wall": "#1f2937",
    "room": "#e6eef8",
    "roomEdge": "#a9c0da",
    "door": "#ea580c",
    "window": "#0891b2",
    "high_window": "#0891b2",
    "fire_door": "#dc2626",
    "stair": "#7c3aed",
    "axis": "#b6c2d1",
}

KIND_LABEL = {
    "door": "门",
    "fire_door": "防火门",
    "window": "窗",
    "high_window": "高窗",
    "unknown": "洞口",
}

LAYERS = [
    ("line", "原图线稿"),
    ("wall", "墙体"),
    ("room", "房间"),
    ("opening", "门窗"),
    ("stair", "楼梯"),
    ("axis", "轴线"),
    ("label", "房间名"),
]

# Kept in step with ``scene.CATEGORIES``; the page draws them in this order.
MODEL_LAYERS = [
    ("wall", "墙体", "#e7e5e4"),
    ("column", "柱", "#9ca3af"),
    ("slab", "楼板", "#cbd5e1"),
    ("stair", "楼梯", "#a78bfa"),
    ("door", "门", "#f97316"),
    ("window", "窗", "#38bdf8"),
    ("room", "房间", "#86efac"),
    ("roof", "屋面板", "#94a3b8"),
    ("parapet", "女儿墙", "#a8a29e"),
]


@dataclass
class PlanView:
    title: str
    bounds: tuple[float, float, float, float]
    walls: list[Any] = field(default_factory=list)
    rooms: list[Any] = field(default_factory=list)
    openings: list[Any] = field(default_factory=list)
    stairs: list[Any] = field(default_factory=list)
    grid: Any = None
    report: Any = None
    segments: list[Any] = field(default_factory=list)


def _y(value: float) -> float:
    """SVG y grows downward; drawing y grows upward. Flip once, here."""
    return -value


def _points(polygon) -> str:
    return " ".join(f"{x:.1f},{_y(y):.1f}" for x, y in polygon.exterior.coords)


def _svg(view: PlanView, index: int) -> str:
    x0, y0, x1, y1 = view.bounds
    width = max(x1 - x0, 1.0)
    height = max(y1 - y0, 1.0)
    parts = [
        f'<svg class="plan" data-plan="{index}" viewBox="{x0:.1f} {_y(y1):.1f} {width:.1f} {height:.1f}" '
        f'preserveAspectRatio="xMidYMid meet" role="img" aria-label="{html.escape(view.title or "平面图")}">'
    ]

    if view.segments:
        # The original linework, underneath everything: the reading is drawn on
        # top of what it claims to have read, so a mismatch is visible rather
        # than argued about.
        path = " ".join(
            f"M{ax:.0f} {_y(ay):.0f}L{bx:.0f} {_y(by):.0f}"
            for ax, ay, bx, by in view.segments
        )
        parts.append(f'<path d="{path}" class="linework" data-layer="line"/>')

    if view.grid is not None and (view.grid.columns or view.grid.rows):
        parts.append('<g data-layer="axis">')
        for x in view.grid.columns:
            parts.append(
                f'<line x1="{x:.1f}" y1="{_y(y0):.1f}" x2="{x:.1f}" y2="{_y(y1):.1f}" class="axis"/>'
            )
        for y in view.grid.rows:
            parts.append(
                f'<line x1="{x0:.1f}" y1="{_y(y):.1f}" x2="{x1:.1f}" y2="{_y(y):.1f}" class="axis"/>'
            )
        parts.append("</g>")

    parts.append('<g data-layer="room">')
    for room in view.rooms:
        if room.polygon is None:
            continue
        tip = f"{room.name or '未命名'}　{room.area:.1f} m²"
        parts.append(
            f'<polygon points="{_points(room.polygon)}" class="room"><title>{html.escape(tip)}</title></polygon>'
        )
    parts.append("</g>")

    parts.append('<g data-layer="wall">')
    for wall in view.walls:
        parts.append(f'<polygon points="{_points(wall)}" class="wall"/>')
    parts.append("</g>")

    parts.append('<g data-layer="stair">')
    for well in view.stairs:
        bx0, by0, bx1, by1 = well.bounds
        parts.append(
            f'<rect x="{bx0:.1f}" y="{_y(by1):.1f}" width="{bx1 - bx0:.1f}" '
            f'height="{by1 - by0:.1f}" class="stair"/>'
        )
    parts.append("</g>")

    parts.append('<g data-layer="opening">')
    for opening in view.openings:
        import math

        width = getattr(opening, "width", 0.0)
        half = width / 2.0
        cx, cy = opening.center
        angle = opening.angle
        ux, uy = math.cos(angle), math.sin(angle)
        nx, ny = -uy, ux
        kind = getattr(opening, "kind", "unknown")
        colour = COLOURS.get(kind, "#64748b")
        band = 200.0
        # The symbol is a hole through the wall, not a line: four corners on the
        # wall faces, so it reads at the same scale as the wall it cuts.
        corners = [
            (cx - ux * half + nx * band / 2, cy - uy * half + ny * band / 2),
            (cx + ux * half + nx * band / 2, cy + uy * half + ny * band / 2),
            (cx + ux * half - nx * band / 2, cy + uy * half - ny * band / 2),
            (cx - ux * half - nx * band / 2, cy - uy * half - ny * band / 2),
        ]
        points = " ".join(f"{x:.1f},{_y(y):.1f}" for x, y in corners)
        tag = getattr(opening, "tag", "") or KIND_LABEL.get(kind, "")
        parts.append(
            f'<polygon points="{points}" fill="#fff" stroke="{colour}" class="openingBox">'
            f"<title>{html.escape(tag)}　{width:.0f}×{getattr(opening, 'height', 0):.0f}</title></polygon>"
        )

        if kind in {"window", "high_window"}:
            # Glazing: the usual double line down the middle of the opening.
            for offset in (-45.0, 45.0):
                parts.append(
                    f'<line x1="{cx - ux * half + nx * offset:.1f}" '
                    f'y1="{_y(cy - uy * half + ny * offset):.1f}" '
                    f'x2="{cx + ux * half + nx * offset:.1f}" '
                    f'y2="{_y(cy + uy * half + ny * offset):.1f}" '
                    f'stroke="{colour}" class="glazing"/>'
                )
        elif kind in {"door", "fire_door"}:
            # Hinge at one jamb, leaf swung a quarter turn, with its arc.
            hx, hy = cx - ux * half, cy - uy * half
            fx, fy = hx + nx * width, hy + ny * width
            bx, by = hx + ux * width, hy + uy * width
            parts.append(
                f'<path d="M {bx:.1f} {_y(by):.1f} A {width:.1f} {width:.1f} 0 0 1 '
                f'{fx:.1f} {_y(fy):.1f}" fill="none" stroke="{colour}" class="swing"/>'
            )
            parts.append(
                f'<line x1="{hx:.1f}" y1="{_y(hy):.1f}" x2="{fx:.1f}" y2="{_y(fy):.1f}" '
                f'stroke="{colour}" class="leaf"/>'
            )
    parts.append("</g>")

    parts.append('<g data-layer="label">')
    for room in view.rooms:
        if not room.name:
            continue
        parts.append(
            f'<text x="{room.center[0]:.1f}" y="{_y(room.center[1]):.1f}" class="roomLabel">'
            f"{html.escape(room.name)}</text>"
        )
    parts.append("</g>")

    parts.append("</svg>")
    return "".join(parts)


def _report_html(view: PlanView) -> str:
    report = view.report
    if report is None:
        return ""
    rows: list[str] = []

    def row(key: str, value: str) -> None:
        rows.append(f'<div class="kv"><dt>{html.escape(key)}</dt><dd>{value}</dd></div>')

    if report.scale:
        row("比例", html.escape(report.scale))
    if report.sheet_kinds:
        row("图纸类型", "、".join(report.sheet_kinds))
    grid = report.grid
    if grid.columns or grid.rows:
        row("定位轴线", f"横向 {len(grid.columns)} 道 · 纵向 {len(grid.rows)} 道")
        if grid.column_spacing:
            row("开间", "、".join(f"{value:.0f}" for value in grid.column_spacing[:10]))
        if grid.row_spacing:
            row("进深", "、".join(f"{value:.0f}" for value in grid.row_spacing[:10]))
    thickness = "、".join(f"{size}×{count}" for size, count in list(report.thickness.items())[:5])
    row(
        "墙体",
        f"{report.wall_count} 面　外 {report.external_walls} / 内 {report.internal_walls}"
        + (f"<br><span class='muted'>厚 {thickness} mm</span>" if thickness else ""),
    )
    if report.rooms:
        row("房间", f"{len(report.rooms)} 间　{sum(r.area for r in report.rooms):.1f} m²")
    summary = report.opening_summary
    if summary.get("total"):
        row("门窗", f"{summary['total']} 个 / {summary['types']} 种")
    if report.stairs:
        row("楼梯", f"{report.stairs} 部　{report.stair_size}")
    if report.elevations:
        row("标高", "、".join(f"{value / 1000:.3f}" for value in report.elevations[:8]))

    rooms = "".join(
        f"<tr><td>{html.escape(room.name or '—')}</td>"
        f"<td class='num'>{room.area:.1f}</td></tr>"
        for room in report.rooms[:60]
    )
    rooms_html = (
        "<table class='rooms'><thead><tr><th>房间</th><th>面积 m²</th></tr></thead>"
        f"<tbody>{rooms}</tbody></table>"
        if rooms
        else ""
    )
    checks = "".join(f"<li>{html.escape(check)}</li>" for check in report.checks)
    checks_html = f"<ul class='checks'>{checks}</ul>" if checks else ""
    return f"<dl class='kvlist'>{''.join(rows)}</dl>{checks_html}{rooms_html}"


def _model_js() -> str:
    """The viewer script, embedded so the page stays one file."""
    return _script("model_view.js")


def _shell_js() -> str:
    """The page's own wiring: open a drawing, watch the read."""
    return _script("shell_view.js")


def _script(name: str) -> str:
    source = Path(__file__).with_name(name)
    return source.read_text(encoding="utf-8") if source.exists() else ""


def _ensure_three(target_dir: Path) -> bool:
    """Put the three.js module beside the page; the page has no network."""
    vendor = target_dir / "vendor"
    destination = vendor / "three.module.js"
    if destination.exists() and destination.stat().st_size > 100_000:
        return True
    here = Path(__file__).resolve()
    for candidate in (
        here.parents[2] / "vendor" / "three.module.js",
        here.parent / "vendor" / "three.module.js",
    ):
        if candidate.exists() and candidate.stat().st_size > 100_000:
            vendor.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(candidate, destination)
            return True
    return False


def write_html(
    views: list[PlanView],
    out_path: str,
    *,
    source: str = "",
    step_path: str = "",
    viewer_url: str = "http://127.0.0.1:3245/",
    scene: dict[str, Any] | None = None,
) -> str:
    """Render the reading as one self-contained page."""
    if not views:
        raise ValueError("没有可显示的平面。")

    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    plans = [
        {
            "title": view.title or f"第 {index + 1} 张",
            "report": _report_html(view),
            "svg": _svg(view, index),
        }
        for index, view in enumerate(views)
    ]
    total_rooms = sum(len(view.rooms) for view in views)
    total_area = sum(sum(room.area for room in view.rooms) for view in views)

    has_model = scene is not None and bool(scene.get("items"))
    if has_model and not _ensure_three(target.parent):
        # No module on disk: a 3D tab that cannot load is worse than none.
        has_model = False
        scene = None

    frame = ""
    if step_path and not has_model:
        frame = html.escape(f"{viewer_url}?file={step_path.replace(chr(92), '/')}")

    layer_boxes = "".join(
        f'<label><input type="checkbox" data-toggle="{key}" checked>{label}</label>'
        for key, label in LAYERS
    )
    model_boxes = "".join(
        f'<label><input type="checkbox" data-cat="{key}" checked>'
        f'<i class="swatch" style="background:{colour}"></i>{label}'
        f'<span class="count" data-count="{key}"></span></label>'
        for key, label, colour in MODEL_LAYERS
    )

    scene_json = "null"
    if scene is not None:
        scene_json = json.dumps(scene, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    model_pane = (
        f'<div class="pane" id="modelPane" hidden>'
        f'<canvas id="view3d"></canvas>'
        f'<div class="hoverTag" id="hoverTag" hidden></div>'
        f'<div class="viewbar">'
        f'<button data-view="iso" class="active">轴测</button>'
        f'<button data-view="front">正立面</button>'
        f'<button data-view="side">侧立面</button>'
        f'<button data-view="top">俯视</button>'
        f'<button data-view="fit">全景</button>'
        f"</div>"
        f'<div class="hint">左键旋转 · 滚轮缩放 · 右键平移 · 单击构件看名称</div>'
        f"</div>"
    )
    if not has_model:
        if frame:
            model_pane = (
                f'<div class="pane" id="modelPane" hidden>'
                f'<iframe id="modelFrame" title="三维模型" allow="fullscreen"></iframe>'
                f'<div class="hint">三维模型来自本机 CAD 查看器（127.0.0.1:3245）</div></div>'
            )
        else:
            model_pane = (
                '<div class="pane" id="modelPane" hidden><div class="empty">'
                "没有三维场景。用 <code>--step</code> 指定 STEP，或用识图前端打开一张 DXF。"
                "</div></div>"
            )

    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>重装武神 · {html.escape(Path(source).name or "图纸")}</title>
<style>
  :root {{
    color-scheme: light;
    --ink:#0f172a; --muted:#64748b; --line:#e2e8f0; --bg:#f1f5f9; --panel:#ffffff;
    --accent:#0f766e;
  }}
  * {{ box-sizing: border-box; }}
  [hidden] {{ display:none !important; }}
  html, body {{ height: 100%; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
          font:14px/1.55 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
          display:flex; flex-direction:column; }}
  header.top {{ display:flex; align-items:center; gap:16px; padding:10px 18px;
                background:#0f172a; color:#f8fafc; flex:0 0 auto; }}
  .brand {{ font-weight:600; letter-spacing:.06em; }}
  a.brand {{ color:inherit; text-decoration:none; }}
  a.brand:hover {{ color:#7dd3fc; }}
  a.back {{ color:#94a3b8; text-decoration:none; font-size:12.5px; border:1px solid #334155;
            border-radius:999px; padding:3px 11px; }}
  a.back:hover {{ color:#f8fafc; border-color:#64748b; }}
  .file {{ color:#94a3b8; font-size:13px; overflow:hidden; text-overflow:ellipsis;
           white-space:nowrap; max-width:36vw; }}
  .summary {{ color:#cbd5e1; font-size:13px; margin-left:auto; }}
  .tabs {{ display:flex; gap:2px; background:#1e293b; border-radius:8px; padding:2px; }}
  .tabs button {{ border:0; background:transparent; color:#cbd5e1; padding:6px 14px;
                  border-radius:6px; cursor:pointer; font:inherit; font-size:13px; }}
  .tabs button.active {{ background:#f8fafc; color:#0f172a; }}
  .ghost {{ border:1px solid #334155; background:transparent; color:#e2e8f0;
            border-radius:7px; padding:5px 12px; font:inherit; font-size:13px; cursor:pointer; }}
  .ghost:hover {{ background:#1e293b; }}
  header .primary {{ border:0; background:var(--accent); color:#fff; border-radius:7px;
                     padding:6px 14px; font:inherit; font-size:13px; cursor:pointer;
                     font-weight:600; }}
  header .primary:hover {{ background:#0d9488; }}
  .lastNote {{ background:#ecfdf5; border:1px solid #a7f3d0; color:#065f46;
               border-radius:7px; padding:7px 10px; font-size:12.5px; margin-bottom:12px; }}
  .lastNote b {{ font-weight:600; }}
  main {{ flex:1 1 auto; display:grid; grid-template-columns:250px minmax(0,1fr) 330px;
          gap:0; min-height:0; }}
  aside {{ background:var(--panel); overflow:auto; }}
  aside.rail {{ border-right:1px solid var(--line); }}
  aside.report {{ border-left:1px solid var(--line); padding:14px 16px; }}
  aside.report h3 {{ font-size:12px; text-transform:uppercase; letter-spacing:.08em;
                     color:var(--muted); margin:14px 0 6px; font-weight:600; }}
  .rail h3 {{ font-size:12px; text-transform:uppercase; letter-spacing:.08em;
              color:var(--muted); margin:14px 14px 6px; font-weight:600; }}
  #planList {{ list-style:none; margin:0; padding:0 8px; }}
  #planList button {{ width:100%; text-align:left; border:0; background:transparent;
                      padding:8px 10px; border-radius:7px; cursor:pointer; font:inherit;
                      color:var(--ink); }}
  #planList button:hover {{ background:#f1f5f9; }}
  #planList button.active {{ background:#ecfdf5; color:#065f46; font-weight:600; }}
  .layers {{ display:flex; flex-direction:column; gap:2px; padding:0 14px 18px; }}
  .layers label {{ display:flex; align-items:center; gap:8px; padding:3px 0;
                   color:#334155; font-size:13px; cursor:pointer; }}
  .swatch {{ width:10px; height:10px; border-radius:3px; display:inline-block;
             border:1px solid rgba(15,23,42,.15); }}
  .layers .count {{ margin-left:auto; color:var(--muted); font-size:12px;
                    font-variant-numeric:tabular-nums; }}
  .chips {{ display:flex; flex-wrap:wrap; gap:6px; padding:0 14px; }}
  .chips button {{ border:1px solid var(--line); background:#fff; color:#334155;
                   border-radius:999px; padding:4px 11px; font:inherit; font-size:12.5px;
                   cursor:pointer; }}
  .chips button.active {{ background:#0f172a; border-color:#0f172a; color:#fff; }}
  .slider {{ padding:4px 14px 16px; }}
  .slider label {{ display:flex; justify-content:space-between; font-size:12px;
                   color:var(--muted); margin-bottom:2px; }}
  .slider input {{ width:100%; accent-color:var(--accent); }}
  .stage {{ position:relative; min-height:0; background:#fff; }}
  .stage .pane {{ position:absolute; inset:0; }}
  #modelPane {{ background:#0b1220; }}
  #view3d {{ width:100%; height:100%; display:block; touch-action:none; }}
  .stage .plan {{ width:100%; height:100%; display:none; padding:16px; }}
  .stage .plan.active {{ display:block; }}
  iframe {{ width:100%; height:100%; border:0; display:block; }}
  .wall {{ fill:{COLOURS['wall']}; fill-opacity:.9; }}
  .room {{ fill:{COLOURS['room']}; stroke:{COLOURS['roomEdge']}; stroke-width:1;
           vector-effect:non-scaling-stroke; }}
  .openingBox {{ stroke-width: 1.2; vector-effect: non-scaling-stroke; }}
  .glazing {{ stroke-width: 0.9; vector-effect: non-scaling-stroke; }}
  .swing {{ stroke-width: 0.9; stroke-dasharray: 6 4; vector-effect: non-scaling-stroke; }}
  .leaf {{ stroke-width: 1.6; vector-effect: non-scaling-stroke; }}
  .stair {{ fill:none; stroke:{COLOURS['stair']}; stroke-width:1.5;
            stroke-dasharray:7 5; vector-effect:non-scaling-stroke; }}
  .axis {{ stroke:{COLOURS['axis']}; stroke-width:.7; stroke-dasharray:14 5 3 5;
           vector-effect:non-scaling-stroke; }}
  .linework {{ fill:none; stroke:#94a3b8; stroke-width:.45; stroke-opacity:.75;
               vector-effect:non-scaling-stroke; }}
  .roomLabel {{ font-size:560px; fill:#334155; text-anchor:middle;
                dominant-baseline:middle; pointer-events:none; }}
  dl.kvlist {{ margin:0; }}
  .kv {{ display:grid; grid-template-columns:74px 1fr; gap:8px; padding:5px 0;
         border-bottom:1px solid #f1f5f9; }}
  .kv dt {{ color:var(--muted); font-size:12px; }}
  .kv dd {{ margin:0; font-size:13px; }}
  .muted {{ color:var(--muted); font-size:12px; }}
  ul.checks {{ margin:12px 0; padding-left:16px; color:#b45309; font-size:12px; }}
  table.rooms {{ width:100%; border-collapse:collapse; margin-top:12px; font-size:12.5px; }}
  table.rooms th {{ text-align:left; color:var(--muted); font-weight:500;
                    border-bottom:1px solid var(--line); padding:4px 6px; }}
  table.rooms td {{ padding:3px 6px; border-bottom:1px solid #f8fafc; }}
  td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .hint {{ position:absolute; inset:auto 0 0 0; padding:10px 14px; font-size:12px;
           color:var(--muted); background:#f8fafc; border-top:1px solid var(--line); }}
  #modelPane .hint {{ background:rgba(15,23,42,.72); border-top:0; color:#94a3b8;
                      pointer-events:none; }}
  .viewbar {{ position:absolute; left:14px; bottom:44px; display:flex; gap:4px;
              background:rgba(15,23,42,.72); padding:4px; border-radius:9px; }}
  .viewbar button {{ border:0; background:transparent; color:#cbd5e1; padding:5px 10px;
                     border-radius:6px; font:inherit; font-size:12.5px; cursor:pointer; }}
  .viewbar button:hover {{ background:rgba(148,163,184,.18); }}
  .viewbar button.active {{ background:#f8fafc; color:#0f172a; }}
  .hoverTag {{ position:absolute; pointer-events:none; background:#f8fafc; color:#0f172a;
               font-size:12px; padding:3px 8px; border-radius:6px; white-space:nowrap;
               box-shadow:0 4px 14px rgba(0,0,0,.35); transform:translate(12px,12px); }}
  .empty {{ position:absolute; inset:0; display:grid; place-items:center; color:#94a3b8;
            background:#0b1220; font-size:13px; text-align:center; padding:40px; }}
  .empty code {{ background:#1e293b; padding:1px 5px; border-radius:4px; }}
  .pickCard {{ border:1px solid var(--line); border-radius:9px; padding:10px 12px;
               margin-bottom:10px; }}
  .pickCard .name {{ font-weight:600; font-size:13.5px; margin-bottom:2px; }}
  .pickCard .meta {{ color:var(--muted); font-size:12px; }}
  .statRow {{ display:flex; justify-content:space-between; padding:3px 0;
              border-bottom:1px solid #f8fafc; font-size:12.5px; }}
  .statRow span:last-child {{ font-variant-numeric:tabular-nums; color:#334155; }}
  .modal {{ position:fixed; inset:0; background:rgba(15,23,42,.55); display:grid;
            place-items:center; z-index:20; }}
  .dialog {{ background:#fff; border-radius:12px; padding:20px 22px; width:min(560px,92vw);
             box-shadow:0 24px 60px rgba(0,0,0,.35); }}
  .dialog h2 {{ margin:0 0 4px; font-size:16px; }}
  .dialog p {{ margin:0 0 14px; color:var(--muted); font-size:12.5px; }}
  .dialog label {{ display:block; font-size:12.5px; color:#334155; margin-bottom:10px; }}
  .dialog input[type=text], .dialog input[type=file] {{ width:100%; margin-top:4px;
      border:1px solid var(--line); border-radius:7px; padding:7px 9px; font:inherit;
      font-size:13px; background:#f8fafc; }}
  .dialog .row {{ display:flex; gap:8px; margin-top:16px; }}
  .dialog button {{ border:1px solid var(--line); background:#fff; color:#0f172a;
                    border-radius:7px; padding:7px 16px; font:inherit; cursor:pointer; }}
  .dialog button.primary {{ background:var(--accent); border-color:var(--accent); color:#fff; }}
  .dialog button:disabled {{ opacity:.55; cursor:default; }}
  .jobLog {{ margin-top:12px; font-size:12px; color:var(--muted); max-height:150px;
             overflow:auto; }}
  .jobLog div {{ padding:1px 0; }}
  .kernel {{ padding:0 14px 18px; }}
  .kernel .wide {{ width:100%; margin-bottom:6px; border:1px solid var(--line);
                   background:#fff; color:#0f172a; border-radius:7px; padding:7px 10px;
                   font:inherit; font-size:13px; cursor:pointer; }}
  .kernel .wide:hover {{ background:#f1f5f9; }}
  .kernel .wide:disabled {{ opacity:.5; cursor:default; }}
  .kernel .wide.secondary {{ border-color:#0f172a; font-weight:600; }}
  .kernelNote {{ color:var(--muted); font-size:12px; line-height:1.5; padding:2px 14px 0; }}
  .kernel .kernelNote {{ padding:2px 0 0; }}
  .kernel .jobLog {{ max-height:120px; }}
  @media (max-width: 1100px) {{
    main {{ grid-template-columns:200px minmax(0,1fr); }}
    aside.report {{ display:none; }}
  }}
</style></head>
<body>
<header class="top">
  <a class="brand" href="/" title="回到首页">重装武神</a>
  <a class="back" href="/" title="回到首页">← 首页</a>
  <button class="primary" id="openBtn" hidden>添加图纸</button>
  <span class="file">{html.escape(source) or '—'}</span>
  <span class="summary">{len(views)} 张平面 · {total_rooms} 间房 · {total_area:.0f} m²</span>
  <nav class="tabs">
    <button id="tabRead" class="active">识图</button>
    <button id="tabModel"{'' if has_model else ' hidden'}>模型</button>
  </nav>
</header>
<main>
  <aside class="rail">
    <div id="railRead">
      <h3>平面</h3>
      <ul id="planList"></ul>
      <h3>图层</h3>
      <div class="layers">{layer_boxes}</div>
    </div>
    <div id="railModel" hidden>
      <h3>楼层</h3>
      <div class="chips" id="floorList"></div>
      <h3>构件</h3>
      <div class="layers">{model_boxes}</div>
      <h3>爆炸</h3>
      <div class="slider">
        <label><span>楼层分离</span><span id="explodeValue">0%</span></label>
        <input type="range" id="explode" min="0" max="100" value="0">
      </div>
      <h3>剖切</h3>
      <div class="slider">
        <label><span>剖切高度</span><span id="cutValue">关</span></label>
        <input type="range" id="cut" min="0" max="100" value="100">
      </div>
      <h3>内核文件</h3>
      <div class="kernel" id="kernelBox" hidden>
        <button class="wide" id="exportStep">导出 STEP</button>
        <button class="wide" id="exportBoth">STEP + IFC</button>
        <button class="wide" id="openViewer">用 CAD 查看器看 STEP</button>
        <div class="kernelNote" id="kernelNote">交给 CAD 内核做成实体，可导入 Revit。约 40 秒。</div>
        <div class="jobLog" id="exportLog"></div>
      </div>
    </div>
  </aside>
  <section class="stage">
    <div class="pane" id="readPane">{''.join(plan['svg'] for plan in plans)}</div>
    {model_pane}
  </section>
  <aside class="report">
    <div id="reportRead">
      <h3 id="reportTitle">{html.escape(plans[0]['title'])}</h3>
      <div id="reportBody">{plans[0]['report']}</div>
    </div>
    <div id="reportModel" hidden>
      <h3>构件</h3>
      <div id="pick"><div class="pickCard"><div class="meta">在模型里单击一个构件。</div></div></div>
      <h3>统计</h3>
      <div id="sceneStats"></div>
    </div>
  </aside>
</main>

<div class="modal" id="openModal" hidden>
  <div class="dialog">
    <h2>添加图纸</h2>
    <p>选一个 DXF（也可以直接把文件拖进窗口）。本机服务会识图、装配、
       生成三维场景，然后把页面换掉。</p>
    <div class="lastNote" id="lastNote" hidden></div>
    <label>DXF 文件<input type="file" id="dxfFile" accept=".dxf"></label>
    <label>或者服务器上的路径<input type="text" id="dxfPath" placeholder="D:\\...\\建施图.dxf"></label>
    <label>首层单独文件（可选）<input type="text" id="groundPath" placeholder="D:\\...\\一层块.dxf"></label>
    <div class="row">
      <button class="primary" id="runJob" disabled>识图并建模</button>
      <button id="closeModal">取消</button>
    </div>
    <div class="jobLog" id="jobLog"></div>
  </div>
</div>

<script type="application/json" id="sceneData">{scene_json}</script>
<script>
const PLANS = {json.dumps([{'title': p['title'], 'report': p['report']} for p in plans], ensure_ascii=False)};
const MODEL_URL = {json.dumps(frame)};
let current = 0;

const list = document.getElementById('planList');
PLANS.forEach((plan, index) => {{
  const li = document.createElement('li');
  const button = document.createElement('button');
  button.textContent = plan.title;
  button.className = index === 0 ? 'active' : '';
  button.addEventListener('click', () => selectPlan(index));
  li.appendChild(button);
  list.appendChild(li);
}});

function selectPlan(index) {{
  current = index;
  list.querySelectorAll('button').forEach((button, i) => {{
    button.className = i === index ? 'active' : '';
  }});
  document.querySelectorAll('.stage .plan').forEach((svg, i) => {{
    svg.classList.toggle('active', i === index);
  }});
  document.getElementById('reportTitle').textContent = PLANS[index].title;
  document.getElementById('reportBody').innerHTML = PLANS[index].report;
  applyLayers();
}}

document.querySelectorAll('input[data-toggle]').forEach((box) => {{
  box.addEventListener('change', applyLayers);
}});

function applyLayers() {{
  const svg = document.querySelector('.stage .plan.active');
  if (!svg) return;
  document.querySelectorAll('input[data-toggle]').forEach((box) => {{
    svg.querySelectorAll(`[data-layer="${{box.dataset.toggle}}"]`).forEach((layer) => {{
      layer.style.display = box.checked ? '' : 'none';
    }});
  }});
}}

const tabRead = document.getElementById('tabRead');
const tabModel = document.getElementById('tabModel');
const readPane = document.getElementById('readPane');
const modelPane = document.getElementById('modelPane');
const railRead = document.getElementById('railRead');
const railModel = document.getElementById('railModel');
const reportRead = document.getElementById('reportRead');
const reportModel = document.getElementById('reportModel');
let modelLoaded = false;

tabRead.addEventListener('click', () => {{
  tabRead.classList.add('active'); tabModel.classList.remove('active');
  readPane.hidden = false; modelPane.hidden = true;
  railRead.hidden = false; railModel.hidden = true;
  reportRead.hidden = false; reportModel.hidden = true;
}});

tabModel.addEventListener('click', () => {{
  tabModel.classList.add('active'); tabRead.classList.remove('active');
  readPane.hidden = true; modelPane.hidden = false;
  railRead.hidden = true; railModel.hidden = false;
  reportRead.hidden = true; reportModel.hidden = false;
  if (!modelLoaded) {{
    modelLoaded = true;
    const iframe = document.getElementById('modelFrame');
    if (iframe && MODEL_URL) iframe.src = MODEL_URL;
    if (window.init3d) window.init3d();
  }}
}});

selectPlan(0);
</script>
<script>
const CAT_COLOUR = {json.dumps({key: colour for key, _label, colour in MODEL_LAYERS})};
const CAT_LABEL = {json.dumps({key: label for key, label, _colour in MODEL_LAYERS})};
{_model_js()}
{_shell_js()}
</script>
</body></html>"""
    target.write_text(document, encoding="utf-8")
    return str(target)
