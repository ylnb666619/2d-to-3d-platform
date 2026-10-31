"""Command line: inspect → classify → walls → massing → overlay."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from . import __version__


def _emit(payload: dict[str, Any], as_json: bool, lines: list[str] | None = None) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        for line in lines or []:
            print(line)


def _fail(message: str, as_json: bool, code: int = 1) -> int:
    if as_json:
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False, indent=2))
    else:
        print(f"错误：{message}", file=sys.stderr)
    return code


def _write(path: str | None, payload: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


# --- inspect ---------------------------------------------------------------


def cmd_inspect(args: argparse.Namespace) -> int:
    from .document import DrawingReadError, read_document

    try:
        info = read_document(args.path, region_cells=args.region_cells)
    except DrawingReadError as exc:
        return _fail(str(exc), args.json)
    payload = info.to_dict()
    payload["ok"] = True
    _write(args.out, payload)

    lines = [
        f"文件：{info.path}",
        f"DXF 版本：{info.dxf_version}　实体：{info.entity_count}",
        f"单位：{info.units.name or '未声明'}（$INSUNITS={info.units.insunits}，"
        f"换算到毫米：{info.units.to_mm if info.units.to_mm is not None else '未知'}）",
    ]
    if info.bounds:
        x0, y0, x1, y1 = info.bounds
        lines.append(f"范围：({x0:.1f}, {y0:.1f}) – ({x1:.1f}, {y1:.1f})　宽 {x1 - x0:.0f} × 高 {y1 - y0:.0f}")
    lines.append(f"图层：{len(info.layers)}　块引用：{len(info.blocks)}　文字：{len(info.texts)}　标注：{len(info.dimensions)}")
    lines.append("")
    lines.append("实体类型：")
    for kind, count in sorted(info.entity_types.items(), key=lambda kv: -kv[1])[:10]:
        lines.append(f"  {kind:<18}{count}")
    lines.append("")
    lines.append(f"区域（疑似图幅）：{len(info.regions)}")
    for region in info.regions[:12]:
        b = region.bounds
        lines.append(
            f"  #{region.index:<2} 实体 {region.entity_count:<6} "
            f"范围 ({b[0]:.0f},{b[1]:.0f})–({b[2]:.0f},{b[3]:.0f})  "
            f"图层数 {len(region.layers)}"
        )
    if len(info.regions) > 12:
        lines.append(f"  …还有 {len(info.regions) - 12} 个")
    lines.append("")
    lines.append("图层（按实体数）：")
    for layer in info.layers[:25]:
        kinds = ", ".join(f"{k}×{v}" for k, v in list(sorted(layer.kinds.items(), key=lambda kv: -kv[1]))[:4])
        lines.append(f"  {layer.name:<28}{layer.count:>7}  {kinds}")
    if len(info.layers) > 25:
        lines.append(f"  …还有 {len(info.layers) - 25} 个图层，用 --json 查看全部")
    if info.blocks:
        lines.append("")
        lines.append("主要块引用：")
        counts: dict[str, int] = {}
        for block in info.blocks:
            counts[block.name] = counts.get(block.name, 0) + 1
        for name, count in sorted(counts.items(), key=lambda kv: -kv[1])[:15]:
            lines.append(f"  {name:<32}{count}")
    if info.warnings:
        lines.append("")
        lines.append("注意：")
        for warning in info.warnings:
            lines.append(f"  - {warning}")
    _emit(payload, args.json, lines)
    return 0


# --- classify --------------------------------------------------------------


def cmd_classify(args: argparse.Namespace) -> int:
    from .document import DrawingReadError, read_document
    from .layers import classify, load_config, summary

    try:
        config = load_config(args.config)
        info = read_document(args.path)
    except (DrawingReadError, FileNotFoundError, ValueError) as exc:
        return _fail(str(exc), args.json)
    classify(info, config)
    result = summary(info)
    payload = {
        "ok": True,
        "config": config["source"],
        "layers": [layer.to_dict() for layer in info.layers],
        **result,
    }
    _write(args.out, payload)

    lines = [f"配置：{config['source']}", ""]
    for role in config["priority"]:
        bucket = result["byRole"].get(role)
        if not bucket:
            continue
        lines.append(f"{role:<8}{bucket['entities']:>8} 实体   " + "、".join(bucket["layers"][:8]))
    lines.append("")
    if result["unmatched"]:
        lines.append(f"未匹配图层（{len(result['unmatched'])} 个，建议补进配置）：")
        for name in result["unmatched"][:40]:
            layer = info.layer(name)
            lines.append(f"  {name:<30}{layer.count if layer else 0}")
    else:
        lines.append("所有图层都已匹配。")
    _emit(payload, args.json, lines)
    return 0


# --- walls -----------------------------------------------------------------


def _walls_payload(result) -> dict[str, Any]:
    payload = result.to_dict()
    payload["ok"] = True
    return payload


def cmd_walls(args: argparse.Namespace) -> int:
    from .document import DrawingReadError, read_document
    from .walls import ExtractOptions, extract, region_bounds

    try:
        if args.region:
            info = read_document(args.path, region_cells=args.region_cells)
            options = ExtractOptions(
                strategy=args.strategy,
                min_thickness=args.min_thickness,
                max_thickness=args.max_thickness,
                min_length=args.min_length,
                region=region_bounds(info, args.region),
            )
        else:
            options = ExtractOptions(
                strategy=args.strategy,
                min_thickness=args.min_thickness,
                max_thickness=args.max_thickness,
                min_length=args.min_length,
            )
        result = extract(args.path, config_path=args.config, options=options)
    except (DrawingReadError, FileNotFoundError, ValueError) as exc:
        return _fail(str(exc), args.json)

    payload = _walls_payload(result)
    _write(args.out, payload)
    stats = result.stats
    lines = [
        f"策略：{stats.get('strategy')}　墙体图层：{'、'.join(stats.get('wallLayers', [])) or '无'}",
        f"闭合轮廓：{stats.get('closedProfiles', 0)}　墙段：{stats.get('segments', 0)}　"
        f"合并后墙面：{stats.get('polygons', 0)}",
    ]
    thickness = stats.get("thickness") or {}
    if thickness:
        common = "、".join(f"{item['thickness']}mm×{item['count']}" for item in thickness.get("common", [])[:5])
        lines.append(f"墙厚：中位 {thickness.get('median')}mm　常见 {common}")
    if stats.get("pairCoverage") is not None:
        lines.append(f"平行线覆盖率：{stats['pairCoverage']:.0%}（墙体线总长 {stats.get('lineLength', 0):.0f}）")
    if stats.get("totalPolygonArea"):
        lines.append(f"墙面总面积：{stats['totalPolygonArea']:.0f} 平方单位")
    if result.warnings:
        lines.append("")
        lines.append("注意：")
        for warning in result.warnings:
            lines.append(f"  - {warning}")
    _emit(payload, args.json, lines)
    return 0


# --- massing ---------------------------------------------------------------


def _sibling(path: str, schema: str) -> str:
    """``building.ifc`` + IFC2X3 -> ``building-ifc2x3.ifc``."""
    target = Path(path)
    return str(target.with_name(f"{target.stem}-{schema.lower()}{target.suffix}"))


def _replace_ground_floor(plan, path: str, *, default_height: float):
    """Take the ground floor from its own drawing, replacing a borrowed one."""
    from .axes import read_axis_labels
    from .building import Storey
    from .document import open_document, read_document
    from .floors import group_floors
    from .openings import read_openings
    from .schedule import read_schedule
    from .stairs import read_stairs
    from .walls import ExtractOptions, extract

    document, open_warnings = open_document(path)
    try:
        ground_info = read_document(path, document=document, warnings=list(open_warnings), close=False)
        ground_openings = read_openings(document, read_schedule(ground_info.texts))
        ground_stairs = read_stairs(document)
        ground_axes = read_axis_labels(document)
    finally:
        try:
            document.close()
        except Exception:  # noqa: BLE001
            pass

    ground_result = extract(path, options=ExtractOptions())
    groups = group_floors(ground_result.polygons, gap_mm=8_000.0, min_polygons=10)
    if not groups or not plan.storeys:
        return plan
    group = max(groups, key=lambda item: item.area)
    height = plan.storeys[0].height or default_height
    x0, y0, x1, y1 = group.bounds
    from .recognize import find_rooms

    ground_rooms = find_rooms(
        list(group.polygons),
        [
            text
            for text in ground_info.texts
            if not text.position
            or (x0 - 3000 <= text.position[0] <= x1 + 3000 and y0 - 3000 <= text.position[1] <= y1 + 3000)
        ],
    )
    plan.storeys[0] = Storey(
        number=1,
        walls=list(group.polygons),
        base=0.0,
        height=height,
        title=f"一层（{Path(path).name}）",
        center=((x0 + x1) / 2, (y0 + y1) / 2),
        bounds=(x0, y0, x1, y1),
        openings=ground_openings,
        segments=ground_result.segments,
        stairs=ground_stairs,
        axes=ground_axes,
        columns=ground_result.columns,
        rooms=ground_rooms,
    )
    plan.notes = [note for note in plan.notes if "第 1 层" not in note]
    plan.notes.append(f"首层取自 {Path(path).name}")
    return plan


def cmd_massing(args: argparse.Namespace) -> int:
    from .document import DrawingReadError, read_document
    from .massing import MassingOptions, build_step, read_height, write_sidecar
    from .walls import ExtractOptions, extract, region_bounds

    try:
        info = read_document(args.path)
        options = ExtractOptions(
            strategy=args.strategy,
            min_thickness=args.min_thickness,
            max_thickness=args.max_thickness,
            min_length=args.min_length,
            region=region_bounds(info, args.region),
        )
        result = extract(args.path, config_path=args.config, options=options)
        if not result.polygons:
            return _fail("没有提取到任何墙面，无法拉伸。先跑 walls 看看识别情况。", args.json)

        massing_options = MassingOptions()
        reading = read_height(
            info.texts,
            default_mm=massing_options.default_height_mm,
            override_mm=args.height,
        )
        height = reading.value_mm
        source = reading.source
        scale = info.units.to_mm or 1.0

        floor_report = None
        base_elevations = None
        if args.building:
            from .axes import read_axis_labels
            from .building import build_building, match_floors, plan_from_matches
            from .document import open_document
            from .floors import group_floors
            from .openings import read_openings
            from .schedule import read_schedule, reconcile
            from .stairs import read_stairs

            schedule = read_schedule(info.texts)
            document, _ignored = open_document(args.path)
            try:
                openings = read_openings(document, schedule)
                stairs = read_stairs(document)
                axis_labels = read_axis_labels(document)
            finally:
                try:
                    document.close()
                except Exception:  # noqa: BLE001
                    pass

            groups = group_floors(
                result.polygons,
                gap_mm=min(args.floor_gap, 8_000.0),
                min_polygons=args.min_floor_walls,
            )
            matches = match_floors(info.texts, groups)
            plan = plan_from_matches(
                matches,
                reading.elevations_mm or [],
                default_height=height,
                assume_ground=not args.no_assume_ground,
                openings=openings,
                segments=result.segments,
                stairs=stairs,
                axes=axis_labels,
                columns=result.columns,
                texts=info.texts,
            )
            plan.parapet_height = args.parapet

            if args.ground_dxf:
                plan = _replace_ground_floor(plan, args.ground_dxf, default_height=height)

            built = build_building(
                plan,
                args.out,
                scale_to_mm=scale,
                openings=openings,
                segments=result.segments,
                stairs=stairs,
            )
            built["schedule"] = schedule.to_dict()
            built["scheduleCheck"] = reconcile(schedule, openings)
            built["openings"] = {
                "total": len(openings),
                "labelled": sum(1 for opening in openings if not opening.inferred),
                "inferred": sum(1 for opening in openings if opening.inferred),
                "stairs": len(stairs),
            }
            floor_report = plan.report()["storeys"]
            if args.ifc:
                from .ifc_export import build_ifc

                choice = (args.ifc_schema or "IFC4").strip().upper()
                schemas = ["IFC4", "IFC2X3"] if choice == "BOTH" else [choice]
                summaries = []
                for index, schema in enumerate(schemas):
                    target = args.ifc if index == 0 else _sibling(args.ifc, schema)
                    summaries.append(
                        build_ifc(
                            plan,
                            openings,
                            result.segments,
                            target,
                            scale_to_mm=scale,
                            stairs=stairs,
                            schema=schema,
                        )
                    )
                built["ifc"] = summaries[0]
                if len(summaries) > 1:
                    built["ifcAll"] = summaries
        elif args.stack:
            from .floors import group_floors, order_bottom_up
            from .massing import build_step_levels

            groups = group_floors(
                result.polygons,
                gap_mm=args.floor_gap,
                min_polygons=args.min_floor_walls,
            )
            if args.floor_order == "y-desc":
                groups.sort(key=lambda group: -group.centroid_y)
            else:
                groups = order_bottom_up(groups)
            if not groups:
                return _fail("没有找到成组的楼层平面，无法堆叠。试试调大 --floor-gap。", args.json)
            floor_report = [group.to_dict() for group in groups]
            if reading.elevations_mm and len(reading.elevations_mm) - 1 == len(groups):
                base_elevations = reading.elevations_mm
            built = build_step_levels(
                [group.polygons for group in groups],
                args.out,
                floor_height_mm=height,
                scale_to_mm=scale,
                base_elevations=base_elevations,
            )
        else:
            built = build_step(result.polygons, args.out, height_mm=height, scale_to_mm=scale)
    except (DrawingReadError, FileNotFoundError, ValueError) as exc:
        return _fail(str(exc), args.json)

    payload = {
        "ok": True,
        "heightSource": source,
        "heightReading": reading.to_dict(),
        "units": info.units.to_dict(),
        **built,
        "walls": result.stats,
    }
    if floor_report is not None:
        payload["floors"] = floor_report
    sidecar = write_sidecar(built["path"], payload)
    _write(args.out_json, payload)
    lines = [
        f"墙高：{height:.0f}mm（来源：{source}）",
        f"拉伸实体：{built['solids']} 个（失败 {built['failed']}）",
        f"体积：{built['volume']:.0f} 立方毫米",
        f"STEP：{built['path']}",
        f"记录：{sidecar}",
    ]
    if reading.evidence:
        lines.insert(1, f"　　依据：{reading.evidence}")
    if reading.first_floor_mm:
        lines.insert(2, f"　　首层：{reading.first_floor_mm:.0f}mm　层数：{reading.storeys}")
    for level in built.get("levels", []):
        if "level" in level:
            lines.append(
                f"  第 {level['level'] + 1} 层  标高 {level['elevation']:.0f}mm  "
                f"{level['solids']} 个实体  墙面 {level['area'] / 1e6:.1f} m²"
            )
        else:
            lines.append(
                f"  第 {level['floor']} 层  {level.get('title', '')}  "
                f"标高 {level['base']:.0f}mm  层高 {level['height']:.0f}mm  "
                f"{level['wallSolids']} 面墙  洞口 {level.get('openingsCut', 0)}  "
                f"楼梯洞口 {level.get('stairsCut', 0)}  "
                f"楼板 {str(level.get('slabArea', 0)) + ' m²' if level['slab'] else '无'}"
            )
    counts = built.get("openings")
    if counts:
        lines.append(
            f"洞口：{counts['total']} 个（有编号 {counts['labelled']}，"
            f"按门窗表补全 {counts['inferred']}）　楼梯对象：{counts['stairs']}"
        )
    check = built.get("scheduleCheck")
    if check and check["rows"]:
        mismatched = [row for row in check["rows"] if row["delta"] not in (None, 0)]
        lines.append(
            f"门窗表：表内 {check['scheduled']} 樘 / 本图 {check['read']} 个带编号洞口"
            + (f"，{len(mismatched)} 种编号对不上（首层单独成图时会少）" if mismatched else "，全部对得上")
        )
        for conflict in check.get("conflicts") or []:
            scheduled, tagged = conflict["schedule"], conflict["tagSize"]
            lines.append(
                f"  尺寸矛盾：{conflict['tag']} 表内 {scheduled[0]:.0f}×{scheduled[1]:.0f}，"
                f"编号规则 {tagged[0]:.0f}×{tagged[1]:.0f}（按编号切割）"
            )
    for note in built.get("notes", []):
        lines.append(f"  注意：{note}")
    if "ifc" in built:
        summary = built["ifc"]
        lines.append(
            f"IFC：{summary['path']}（{summary['walls']} 面墙 / {summary.get('columns', 0)} 根柱 / "
            f"{summary['openings']} 洞口（门 {summary.get('doors', 0)} / 窗 {summary.get('windows', 0)}） / "
            f"{summary['slabs']} 块楼板 / {summary.get('stairVoids', 0)} 处楼梯洞口 / "
            f"{summary.get('spaces', 0)} 个房间 / {summary['storeys']} 层）"
        )
    if result.warnings:
        lines.append("")
        for warning in result.warnings:
            lines.append(f"  - {warning}")
    _emit(payload, args.json, lines)
    return 0


# --- overlay ---------------------------------------------------------------


def cmd_overlay(args: argparse.Namespace) -> int:
    from .document import DrawingReadError, iter_segments, open_document, read_document
    from .overlay import render_overlay
    from .walls import ExtractOptions, extract, region_bounds

    try:
        info = read_document(args.path)
        region = region_bounds(info, args.region)
        result = extract(
            args.path,
            config_path=args.config,
            options=ExtractOptions(
                strategy=args.strategy,
                min_thickness=args.min_thickness,
                max_thickness=args.max_thickness,
                min_length=args.min_length,
                region=region,
            ),
        )
        document, _warnings = open_document(args.path)
        try:
            segments = list(iter_segments(document))
        finally:
            try:
                document.close()
            except Exception:  # noqa: BLE001
                pass
        paths = render_overlay(segments, result.polygons, args.out, region=region)
    except (DrawingReadError, FileNotFoundError, ValueError) as exc:
        return _fail(str(exc), args.json)

    payload = {"ok": True, **paths, "walls": result.stats}
    _write(args.out_json, payload)
    lines = [
        f"对比图：{paths['png']}",
        f"　　　　{paths['svg']}",
        f"墙体面：{len(result.polygons)}　墙段：{len(result.segments)}",
    ]
    _emit(payload, args.json, lines)
    return 0


# --- schedule --------------------------------------------------------------


def cmd_schedule(args: argparse.Namespace) -> int:
    from .document import DrawingReadError, open_document, read_document
    from .openings import read_openings
    from .schedule import read_schedule, reconcile

    try:
        document, open_warnings = open_document(args.path)
        try:
            info = read_document(args.path, document=document, warnings=list(open_warnings), close=False)
            schedule = read_schedule(info.texts)
            openings = read_openings(document, schedule)
        finally:
            try:
                document.close()
            except Exception:  # noqa: BLE001
                pass
    except (DrawingReadError, FileNotFoundError, ValueError) as exc:
        return _fail(str(exc), args.json)

    check = reconcile(schedule, openings)
    payload = {"ok": True, **schedule.to_dict(), "check": check}
    _write(args.out, payload)

    lines = [f"门窗表：{len(schedule.rows)} 种"]
    for row in schedule.rows:
        lines.append(
            f"  {row.type_label or '':<4}{row.tag:<12}{row.width:>6.0f} × {row.height:<6.0f}"
            f"  {str(row.count) + ' 樘' if row.count is not None else '':<8}{row.kind}"
        )
    lines.append("")
    lines.append(f"对账：表内 {check['scheduled']} 樘，图面读到 {check['read']} 个带编号洞口")
    for row in check["rows"]:
        if row["delta"] in (None, 0):
            continue
        expected = "—" if row["schedule"] is None else row["schedule"]
        lines.append(f"  {row['tag']:<12}表 {expected:<5}图 {row['read']:<5}差 {row['delta']}")
    for conflict in check["conflicts"]:
        scheduled, tagged = conflict["schedule"], conflict["tagSize"]
        lines.append(
            f"  尺寸矛盾：{conflict['tag']} 表内 {scheduled[0]:.0f}×{scheduled[1]:.0f}，"
            f"编号规则 {tagged[0]:.0f}×{tagged[1]:.0f}——按编号切割"
        )
    for note in schedule.notes:
        lines.append(f"注意：{note}")
    _emit(payload, args.json, lines)
    return 0


# --- recognize -------------------------------------------------------------


def cmd_recognize(args: argparse.Namespace) -> int:
    """Read the drawing the way 建筑工程识图 does, one plan at a time."""
    from .document import DrawingReadError
    from .pipeline import read_drawing
    from .webui import write_html

    try:
        reading = read_drawing(
            args.path,
            ground=args.ground_dxf,
            config=args.config,
            group_gap=args.floor_gap,
            min_floor_walls=args.min_floor_walls,
        )
    except (DrawingReadError, FileNotFoundError, ValueError) as exc:
        return _fail(str(exc), args.json)

    if args.html:
        written = write_html(
            reading.views,
            args.html,
            source=args.path,
            step_path=args.step or "",
            viewer_url=args.viewer_url,
            scene=reading.scene,
        )
        print(f"识图页面：{written}")

    payload = {
        "ok": True,
        "path": args.path,
        "plans": [report.to_dict() for report in reading.reports],
    }
    _write(args.out, payload)

    lines = [f"文件：{args.path}　识图 {len(reading.reports)} 张平面"]
    if reading.scene:
        counts = reading.scene["counts"]
        lines.append("三维：" + "、".join(f"{name} {count}" for name, count in counts.items()))
    for index, found in enumerate(reading.reports, start=1):
        lines.append("")
        lines.append(f"── 第 {index} 张：{found.title or '未命名'} ──")
        lines.extend(_recognize_lines(found))
    _emit(payload, args.json, lines)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """The front end's server: the page, and the pipeline behind it."""
    from .server import serve

    root = args.root or str(Path(__file__).resolve().parents[2] / "tmp")
    serve(
        root,
        port=args.port,
        source=args.path or "",
        ground=args.ground_dxf or "",
        open_first=not args.no_start,
    )
    return 0


def _recognize_lines(found) -> list[str]:
    lines: list[str] = []
    head = [f"比例：{found.scale or '未标注'}"]
    if found.sheet_kinds:
        head.append("图纸类型：" + "、".join(found.sheet_kinds))
    lines.append("　".join(head))

    grid = found.grid
    if grid.columns or grid.rows:
        lines.append(
            f"定位轴线：横向 {grid.column_labels[0] if grid.column_labels else '—'}"
            f"–{grid.column_labels[-1] if grid.column_labels else '—'}（{len(grid.columns)} 道）　"
            f"纵向 {grid.row_labels[0] if grid.row_labels else '—'}"
            f"–{grid.row_labels[-1] if grid.row_labels else '—'}（{len(grid.rows)} 道）"
        )
        if grid.column_spacing:
            lines.append("　　开间：" + "、".join(f"{value:.0f}" for value in grid.column_spacing[:12]))
        if grid.row_spacing:
            lines.append("　　进深：" + "、".join(f"{value:.0f}" for value in grid.row_spacing[:12]))
    else:
        lines.append("定位轴线：未识别（无轴号对象）")

    thickness = "、".join(f"{size}mm×{count}" for size, count in list(found.thickness.items())[:5])
    lines.append(
        f"墙体：{found.wall_count} 面（外墙 {found.external_walls} / 内墙 {found.internal_walls}）"
        f"　墙厚 {thickness or '—'}　柱/其他 {found.column_count}"
    )

    if found.rooms:
        total = sum(room.area for room in found.rooms)
        named = sum(1 for room in found.rooms if room.name)
        lines.append(f"房间：{len(found.rooms)} 间（有名称 {named}）　合计 {total:.1f} m²")
        for room in found.rooms[:8]:
            lines.append(
                f"　　{(room.name or '（无名称）')[:12]:<14}{room.area:>7.1f} m²"
                f"　({room.center[0]:.0f},{room.center[1]:.0f})"
            )
        if len(found.rooms) > 8:
            lines.append(f"　　…还有 {len(found.rooms) - 8} 间")
    else:
        lines.append("房间：未识别")

    summary = found.opening_summary
    if summary.get("total"):
        lines.append(f"门窗：{summary['total']} 个 / {summary['types']} 种")
        for row in found.openings[:8]:
            lines.append(
                f"　　{row['tag']:<12}{row['width']:>6.0f}×{row['height']:<6.0f}{row['count']:>4} 个"
                f"　{row['kind']}"
            )
    if found.stairs:
        lines.append(f"楼梯：{found.stairs} 部　梯井 {found.stair_size}")
    if found.elevations:
        shown = "、".join(f"{value / 1000:.3f}" for value in found.elevations[:8])
        lines.append(f"标高：{shown}" + ("…" if len(found.elevations) > 8 else ""))
    if found.checks:
        lines.append("注意：")
        for check in found.checks:
            lines.append(f"　- {check}")
    return lines


# --- parser ----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="massing",
        description="把建筑平面图 DXF 解析成体块模型，并生成与原图的对照图。",
    )
    parser.add_argument("--version", action="version", version=f"massing {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("path", help="DXF 文件路径")
    common.add_argument("--json", action="store_true", help="输出 JSON")
    common.add_argument("--out", help="把结果写到指定 JSON 文件")

    p = sub.add_parser("inspect", parents=[common], help="统计图层、实体、块引用与图幅区域")
    p.add_argument("--region-cells", type=int, default=120, help="图幅聚类网格密度（默认 120）")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("classify", parents=[common], help="按配置判断每个图层的用途")
    p.add_argument("--config", help="图层角色配置 JSON")
    p.set_defaults(func=cmd_classify)

    def wall_options(sub_parser: argparse.ArgumentParser) -> None:
        sub_parser.add_argument("--config", help="图层角色配置 JSON")
        sub_parser.add_argument("--region", help="图幅：序号（如 2）或 bbox:x0,y0,x1,y1")
        sub_parser.add_argument("--strategy", choices=["auto", "proxy", "closed", "pairs"], default="auto")
        sub_parser.add_argument("--min-thickness", type=float, default=60.0)
        sub_parser.add_argument("--max-thickness", type=float, default=600.0)
        sub_parser.add_argument("--min-length", type=float, default=100.0)

    p = sub.add_parser("walls", parents=[common], help="提取墙体候选并给出诊断")
    wall_options(p)
    p.add_argument("--region-cells", type=int, default=120)
    p.set_defaults(func=cmd_walls)

    p = sub.add_parser("massing", parents=[common], help="把墙体拉伸成体块并导出 STEP")
    wall_options(p)
    p.add_argument("--height", type=float, help="墙高（毫米）；不给则从图纸文字读取")
    p.add_argument("--out-json", help="另存一份构建记录的 JSON")
    p.add_argument("--building", action="store_true", help="按楼层标题与标高装配成整栋楼（含楼板与屋顶）")
    p.add_argument("--ground-dxf", help="首层平面的 DXF（首层单独一个文件时）")
    p.add_argument("--parapet", type=float, default=1200.0, help="屋顶女儿墙高度（毫米，0 表示不做，默认 1200）")
    p.add_argument("--ifc", help="同时导出 IFC 文件到该路径（需 ifcopenshell）")
    p.add_argument(
        "--ifc-schema",
        default="IFC4",
        help="IFC 版本：IFC4（默认）/ IFC2X3 / both（Revit 2018 请用 IFC2X3）",
    )
    p.add_argument("--no-assume-ground", action="store_true", help="缺少某层平面时不借用下层")
    p.add_argument("--stack", action="store_true", help="识别多个楼层平面并竖向堆叠")
    p.add_argument("--floor-gap", type=float, default=20000.0, help="楼层分组间距（毫米，默认 20000）")
    p.add_argument("--min-floor-walls", type=int, default=10, help="少于该墙面数的组不算楼层")
    p.add_argument(
        "--floor-order",
        choices=["y-asc", "y-desc"],
        default="y-asc",
        help="楼层顺序：y-asc 表示图纸下方为一层",
    )
    p.set_defaults(func=cmd_massing)

    p = sub.add_parser("schedule", parents=[common], help="读取门窗表，并与图面上的洞口对账")
    p.set_defaults(func=cmd_schedule)

    p = sub.add_parser("serve", help="启动识图前端：页面 + 本机识图服务")
    p.add_argument("path", nargs="?", help="启动时先读这张 DXF")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p.add_argument("--port", type=int, default=3246, help="端口（默认 3246）")
    p.add_argument("--root", help="页面与产物目录（默认 bim\\tmp）")
    p.add_argument("--ground-dxf", help="首层平面的 DXF（首层单独一个文件时）")
    p.add_argument("--no-start", action="store_true", help="启动时不自动读图")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("recognize", parents=[common], help="按识图方法读一张图：图名、轴网、房间、墙体、门窗")
    p.add_argument("--config", help="图层角色配置 JSON")
    p.add_argument("--floor-gap", type=float, default=20000.0, help="图幅分组间距（毫米，默认 20000）")
    p.add_argument("--min-floor-walls", type=int, default=10, help="少于该墙面数的组不算一张平面")
    p.add_argument("--ground-dxf", help="首层平面的 DXF（首层单独一个文件时）")
    p.add_argument("--html", help="同时输出一份可交互的识图页面（HTML）")
    p.add_argument("--step", help="识图页面里「模型」页签要嵌入的 STEP 路径")
    p.add_argument("--viewer-url", default="http://127.0.0.1:3245/", help="本机 CAD 查看器地址")
    p.set_defaults(func=cmd_recognize)

    p = sub.add_parser("overlay", parents=[common], help="生成原图与墙体的叠加对比图")
    wall_options(p)
    p.add_argument("--out-json", help="另存一份对比信息的 JSON")
    p.set_defaults(func=cmd_overlay)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # A GBK console cannot encode "²", and a build must not die on its own
    # report: replace instead of raising.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - a CLI reports, never dumps a traceback
        return _fail(f"{type(exc).__name__}: {exc}", getattr(args, "json", False))


if __name__ == "__main__":
    raise SystemExit(main())
