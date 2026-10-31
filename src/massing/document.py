"""Read a DXF into the intermediate model.

Reading rules follow what the reference implementation learned the hard way:

* try ``ezdxf.readfile`` first, then fall back to ``ezdxf.recover.readfile`` —
  real drawings from other CAD systems are frequently slightly malformed;
* report ``$INSUNITS`` honestly: an unitless drawing is not millimetres, and
  pretending otherwise silently produces a model 1000x too small;
* keep heavy imports inside functions where practical, but this module is
  allowed to import ezdxf at module scope (it is a DXF tool, not a CLI shell).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import ezdxf
import ezdxf.recover

from .model import (
    BlockRef,
    Bounds,
    DimItem,
    DocumentInfo,
    LayerInfo,
    PolylineItem,
    Region,
    TextItem,
    Units,
)


class DrawingReadError(RuntimeError):
    """The file could not be opened as a DXF at all."""


def open_document(path: str | Path) -> tuple[Any, list[str]]:
    """Open a DXF, recovering if the strict reader refuses it."""
    target = str(Path(path))
    warnings: list[str] = []
    try:
        return ezdxf.readfile(target), warnings
    except FileNotFoundError:
        raise DrawingReadError(f"找不到文件：{target}") from None
    except (ezdxf.DXFError, OSError, ValueError, UnicodeDecodeError) as exc:
        warnings.append(f"严格读取失败（{type(exc).__name__}），改用恢复模式读取。")
    try:
        document, auditor = ezdxf.recover.readfile(target)
    except Exception as exc:  # noqa: BLE001 - report the real reason, never a bare traceback
        raise DrawingReadError(f"无法读取 DXF：{exc}") from exc
    errors = len(getattr(auditor, "errors", []) or [])
    fixes = len(getattr(auditor, "fixes", []) or [])
    if errors or fixes:
        warnings.append(f"恢复模式修复了 {fixes} 处问题，发现 {errors} 处错误。")
    return document, warnings


def units(document: Any) -> Units:
    """``$INSUNITS`` as a number, a name, and a factor to millimetres."""
    from ezdxf import units as ezdxf_units

    try:
        code = int(document.header.get("$INSUNITS", 0) or 0)
    except (TypeError, ValueError):
        code = 0
    try:
        name = str(ezdxf_units.unit_name(code))
    except (KeyError, IndexError, TypeError, ValueError):
        name = ""
    try:
        factor = round(float(ezdxf_units.conversion_factor(code, ezdxf_units.MM)), 9)
    except (KeyError, IndexError, TypeError, ValueError, ZeroDivisionError):
        factor = None
    return Units(insunits=code, name=name, to_mm=factor)


def _layer_colour(layer: Any) -> str | None:
    try:
        rgb = layer.rgb
        if rgb is not None:
            return "#%02x%02x%02x" % (rgb[0], rgb[1], rgb[2])
    except Exception:  # noqa: BLE001 - a colour is decoration, never a reason to fail
        pass
    try:
        from ezdxf.colors import aci2rgb

        r, g, b = aci2rgb(int(layer.color))
        return "#%02x%02x%02x" % (r, g, b)
    except Exception:  # noqa: BLE001
        return None


def _bbox(entity: Any) -> Bounds | None:
    try:
        from ezdxf import bbox as ezdxf_bbox

        box = ezdxf_bbox.extents([entity])
        if box.has_data:
            return (box.extmin.x, box.extmin.y, box.extmax.x, box.extmax.y)
    except Exception:  # noqa: BLE001 - a single bad entity must not abort the scan
        return None
    return None


def _text_plain(entity: Any) -> str:
    kind = entity.dxftype()
    try:
        if kind == "MTEXT":
            return entity.plain_text()
        value = getattr(entity.dxf, "text", "")
        return str(value)
    except Exception:  # noqa: BLE001
        return ""


def _text_height(entity: Any) -> float:
    try:
        if entity.dxftype() == "MTEXT":
            return float(entity.dxf.char_height)
        return float(entity.dxf.height)
    except Exception:  # noqa: BLE001
        return 0.0


def _polyline_points(entity: Any) -> tuple[list[tuple[float, float]], bool, list[float]]:
    kind = entity.dxftype()
    if kind == "LWPOLYLINE":
        rows = list(entity.get_points("xyb"))
        points = [(float(x), float(y)) for x, y, _b in rows]
        bulges = [float(b) for _x, _y, b in rows]
        return points, bool(entity.closed), bulges
    if kind == "POLYLINE":
        points = [(float(v.x), float(v.y)) for v in entity.points()]
        return points, bool(entity.is_closed), [0.0] * len(points)
    return [], False, []


def _dimension_measurement(entity: Any) -> float | None:
    try:
        value = float(entity.get_measurement())
        return value
    except Exception:  # noqa: BLE001
        pass
    try:
        value = float(entity.dxf.actual_measurement)
        return value
    except Exception:  # noqa: BLE001
        return None


def _text_item(entity: Any, layer: str, owner: str = "") -> TextItem | None:
    """One TEXT/MTEXT/ATTRIB entity as a :class:`TextItem`, or ``None``."""
    kind = entity.dxftype()
    text = _text_plain(entity).strip()
    if not text:
        return None
    position = None
    try:
        point = entity.dxf.insert
        position = (float(point.x), float(point.y))
    except Exception:  # noqa: BLE001
        position = None
    handle = str(getattr(entity.dxf, "handle", "")) or owner
    return TextItem(
        text=text,
        layer=str(getattr(entity.dxf, "layer", layer)),
        handle=handle,
        position=position,
        height=_text_height(entity),
        kind=kind if not owner else f"{kind}@{owner}",
    )


def _proxy_texts(entity: Any, layer: str) -> list[TextItem]:
    """Text carried inside a custom CAD object's proxy graphic.

    ZWCAD Architecture stores room names, elevation labels and the level table
    as SWR_TEXT objects; nothing on the entity itself holds the string, but the
    proxy graphic that lets other CAD systems draw it does.
    """
    from ezdxf.proxygraphic import ProxyGraphic

    raw = getattr(entity, "proxy_graphic", None)
    if not raw:
        return []
    handle = str(getattr(entity.dxf, "handle", ""))
    items: list[TextItem] = []
    try:
        children = list(ProxyGraphic(raw).virtual_entities())
    except Exception:  # noqa: BLE001
        return []
    for child in children:
        if child.dxftype() in {"TEXT", "MTEXT", "ATTRIB"}:
            item = _text_item(child, layer, owner=f"proxy:{handle}")
            if item is not None:
                items.append(item)
    return items


def _xref_names(document: Any) -> list[str]:
    names: list[str] = []
    for block in document.blocks:
        try:
            flags = int(block.block_record.dxf.flags)
        except Exception:  # noqa: BLE001
            continue
        if flags & 4:  # XREF bit
            names.append(block.name)
    return sorted(set(names))


def _clusters(
    items: list[tuple[int, Bounds, str]],
    extent: Bounds,
    cells_target: int = 120,
) -> list[tuple[Bounds, list[tuple[int, Bounds, str]]]]:
    """Group entity bounding boxes into visually separate sheets.

    A grid cells the model space; entities sharing a cell join one group, and
    touching cells merge through union-find. Sheets in a real drawing are
    separated by white space far larger than any single entity, so this
    separates them reliably without a spatial index dependency.
    """
    if not items:
        return []
    minx, miny, maxx, maxy = extent
    width = max(maxx - minx, 1e-6)
    height = max(maxy - miny, 1e-6)
    cell = max(width, height) / cells_target
    if cell <= 0:
        return [(extent, items)]

    parent: dict[tuple[int, int], tuple[int, int]] = {}

    def find(node: tuple[int, int]) -> tuple[int, int]:
        root = node
        while parent.get(root, root) != root:
            root = parent[root]
        while parent.get(node, node) != node:
            parent[node], node = root, parent[node]
        return root

    def union(a: tuple[int, int], b: tuple[int, int]) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    entity_cells: list[list[tuple[int, int]]] = []
    for _index, box, _layer in items:
        bx0, by0, bx1, by1 = box
        cx0, cy0 = int((bx0 - minx) // cell), int((by0 - miny) // cell)
        cx1, cy1 = int((bx1 - minx) // cell), int((by1 - miny) // cell)
        span = (cx1 - cx0 + 1) * (cy1 - cy0 + 1)
        if span > 64:
            # A long entity: its corners and centre describe where it lies
            # without walking every cell it crosses.
            mids = {((cx0 + cx1) // 2, (cy0 + cy1) // 2)}
            covered = {(cx0, cy0), (cx1, cy0), (cx0, cy1), (cx1, cy1)} | mids
        else:
            covered = {
                (cx, cy)
                for cx in range(cx0, cx1 + 1)
                for cy in range(cy0, cy1 + 1)
            }
        for node in covered:
            parent.setdefault(node, node)
        first = next(iter(covered))
        for node in covered:
            union(first, node)
        entity_cells.append(sorted(covered))

    groups: dict[tuple[int, int], list[tuple[int, Bounds, str]]] = defaultdict(list)
    for item, cells in zip(items, entity_cells):
        groups[find(cells[0])].append(item)

    out: list[tuple[Bounds, list[tuple[int, Bounds, str]]]] = []
    for members in groups.values():
        xs0 = min(m[1][0] for m in members)
        ys0 = min(m[1][1] for m in members)
        xs1 = max(m[1][2] for m in members)
        ys1 = max(m[1][3] for m in members)
        out.append(((xs0, ys0, xs1, ys1), members))
    out.sort(key=lambda pair: pair[0][0])
    return out


def read_document(
    path: str | Path,
    *,
    region_cells: int = 120,
    document: Any | None = None,
    warnings: list[str] | None = None,
    close: bool = True,
) -> DocumentInfo:
    """Read a DXF into :class:`DocumentInfo`.

    Pass ``document`` and ``close=False`` when the caller already opened the
    file and needs the same handle for a second pass.
    """
    if document is None:
        document, warnings = open_document(path)
    elif warnings is None:
        warnings = []
    try:
        modelspace = document.modelspace()
        entity_types: Counter[str] = Counter()
        attributed: dict[str, LayerInfo] = {}
        blocks: list[BlockRef] = []
        texts: list[TextItem] = []
        dimensions: list[DimItem] = []
        polylines: list[PolylineItem] = []
        cluster_items: list[tuple[int, Bounds, str]] = []

        for index, entity in enumerate(modelspace):
            kind = entity.dxftype()
            entity_types[kind] += 1
            layer_name = str(getattr(entity.dxf, "layer", "0"))
            info = attributed.get(layer_name)
            if info is None:
                info = LayerInfo(name=layer_name, color=None)
                attributed[layer_name] = info
            info.count += 1
            info.kinds[kind] = info.kinds.get(kind, 0) + 1

            box = _bbox(entity)
            if box is not None:
                cluster_items.append((index, box, layer_name))

            handle = str(getattr(entity.dxf, "handle", ""))
            if kind == "INSERT":
                try:
                    blocks.append(
                        BlockRef(
                            name=str(entity.dxf.name),
                            handle=handle,
                            layer=layer_name,
                            insert=(float(entity.dxf.insert.x), float(entity.dxf.insert.y)),
                            scale=(
                                float(getattr(entity.dxf, "xscale", 1.0) or 1.0),
                                float(getattr(entity.dxf, "yscale", 1.0) or 1.0),
                            ),
                            rotation=float(getattr(entity.dxf, "rotation", 0.0) or 0.0),
                        )
                    )
                except Exception:  # noqa: BLE001
                    warnings.append(f"块引用 {handle} 缺少插入点，已跳过。")
                # Elevation labels and level tables usually live inside blocks,
                # so the block's own text is read too.
                try:
                    for child in entity.virtual_entities():
                        if child.dxftype() in {"TEXT", "MTEXT", "ATTRIB"}:
                            item = _text_item(child, layer_name, owner=f"block:{handle}")
                            if item is not None:
                                texts.append(item)
                except Exception:  # noqa: BLE001
                    pass
                for attrib in getattr(entity, "attribs", []) or []:
                    item = _text_item(attrib, layer_name, owner=f"attrib:{handle}")
                    if item is not None:
                        texts.append(item)
            elif kind in {"TEXT", "MTEXT"}:
                item = _text_item(entity, layer_name)
                if item is not None:
                    texts.append(item)
            elif kind.startswith("SWR_"):
                texts.extend(_proxy_texts(entity, layer_name))
            elif kind in {"DIMENSION", "ARC_DIMENSION"}:
                position = None
                try:
                    point = entity.dxf.text_midpoint
                    position = (float(point.x), float(point.y))
                except Exception:  # noqa: BLE001
                    try:
                        point = entity.dxf.defpoint
                        position = (float(point.x), float(point.y))
                    except Exception:  # noqa: BLE001
                        position = None
                dimensions.append(
                    DimItem(
                        kind=kind,
                        layer=layer_name,
                        handle=handle,
                        measurement=_dimension_measurement(entity),
                        position=position,
                    )
                )
            elif kind in {"LWPOLYLINE", "POLYLINE"}:
                points, closed, bulges = _polyline_points(entity)
                if len(points) >= 2:
                    polylines.append(
                        PolylineItem(
                            handle=handle,
                            layer=layer_name,
                            points=points,
                            closed=closed,
                            bulges=bulges,
                        )
                    )

        for layer in document.layers:
            name = str(layer.dxf.name)
            info = attributed.get(name)
            if info is None:
                info = LayerInfo(name=name, color=_layer_colour(layer), count=0)
                attributed[name] = info
            else:
                info.color = _layer_colour(layer)

        layer_list = sorted(attributed.values(), key=lambda item: (-item.count, item.name))

        bounds: Bounds | None = None
        if cluster_items:
            bounds = (
                min(item[1][0] for item in cluster_items),
                min(item[1][1] for item in cluster_items),
                max(item[1][2] for item in cluster_items),
                max(item[1][3] for item in cluster_items),
            )

        regions: list[Region] = []
        if bounds is not None:
            for index, (box, members) in enumerate(_clusters(cluster_items, bounds, region_cells), start=1):
                layer_counts = Counter(member[2] for member in members)
                regions.append(
                    Region(index=index, bounds=box, entity_count=len(members), layers=dict(layer_counts))
                )

        xrefs = _xref_names(document)
        if xrefs:
            warnings.append(
                "图纸包含外部参照（XREF）：" + "、".join(xrefs) + "。参照内容不在本文件中，请先绑定后再解析。"
            )
        if not unit_known(document):
            warnings.append(
                "$INSUNITS 未声明单位，坐标按图纸单位处理。若体块尺寸明显不对，请确认图纸是否以毫米绘制。"
            )

        return DocumentInfo(
            path=str(Path(path)),
            dxf_version=str(document.dxfversion),
            units=units(document),
            bounds=bounds,
            entity_count=sum(entity_types.values()),
            layers=layer_list,
            blocks=blocks,
            texts=texts,
            dimensions=dimensions,
            polylines=polylines,
            regions=regions,
            entity_types=dict(entity_types),
            xrefs=xrefs,
            warnings=warnings,
        )
    finally:
        if close:
            try:
                document.close()
            except Exception:  # noqa: BLE001
                pass


def unit_known(document: Any) -> bool:
    code = 0
    try:
        code = int(document.header.get("$INSUNITS", 0) or 0)
    except (TypeError, ValueError):
        code = 0
    return code not in (0, None)


def iter_segments(document: Any, *, layers: Iterable[str] | None = None, flatten_blocks: bool = True):
    """Yield ``((x0, y0), (x1, y1), layer, handle)`` for drawable lines.

    Arcs and circles are sampled; this is for the comparison overlay, not for
    measurement, so a coarse chord tolerance is enough.
    """
    allowed = None if layers is None else {name for name in layers}

    def wanted(layer: str) -> bool:
        return allowed is None or layer in allowed

    def emit(entity: Any):
        kind = entity.dxftype()
        layer = str(getattr(entity.dxf, "layer", "0"))
        handle = str(getattr(entity.dxf, "handle", ""))
        if not wanted(layer):
            return
        if kind == "LINE":
            yield ((float(entity.dxf.start.x), float(entity.dxf.start.y)),
                   (float(entity.dxf.end.x), float(entity.dxf.end.y)), layer, handle)
        elif kind == "LWPOLYLINE":
            points = [(float(x), float(y)) for x, y, _b in entity.get_points("xyb")]
            for a, b in zip(points, points[1:]):
                yield (a, b, layer, handle)
            if entity.closed and len(points) > 2:
                yield (points[-1], points[0], layer, handle)
        elif kind == "POLYLINE":
            points = [(float(v.x), float(v.y)) for v in entity.points()]
            for a, b in zip(points, points[1:]):
                yield (a, b, layer, handle)
            if entity.is_closed and len(points) > 2:
                yield (points[-1], points[0], layer, handle)
        elif kind in {"ARC", "CIRCLE"}:
            import math

            center = entity.dxf.center
            radius = float(entity.dxf.radius)
            start = float(getattr(entity.dxf, "start_angle", 0.0) or 0.0)
            end = float(getattr(entity.dxf, "end_angle", 360.0) or 360.0)
            if kind == "CIRCLE":
                start, end = 0.0, 360.0
            sweep = (end - start) % 360 or 360
            steps = max(8, int(sweep / 10))
            points = []
            for step in range(steps + 1):
                angle = math.radians(start + sweep * step / steps)
                points.append((float(center.x) + radius * math.cos(angle),
                               float(center.y) + radius * math.sin(angle)))
            for a, b in zip(points, points[1:]):
                yield (a, b, layer, handle)

    for entity in document.modelspace():
        if entity.dxftype() == "INSERT" and flatten_blocks:
            try:
                children = list(entity.virtual_entities())
            except Exception:  # noqa: BLE001
                continue
            for child in children:
                yield from emit(child)
        else:
            yield from emit(entity)
