"""Draw the comparison image: original linework under translucent wall areas.

Both layers use DXF world coordinates, so the two are aligned by construction —
there is no registration step to get wrong.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from shapely.geometry import Polygon


def _clip(point, region):
    if region is None:
        return True
    x, y = point
    return region[0] <= x <= region[2] and region[1] <= y <= region[3]


def render_overlay(
    segments: Iterable[tuple[tuple[float, float], tuple[float, float], str, str]],
    polygons: list[Polygon],
    out_base: str | Path,
    *,
    region: tuple[float, float, float, float] | None = None,
    title: str = "Wall massing comparison",
    max_segments: int = 400_000,
) -> dict[str, str]:
    """Write ``<out_base>.png`` and ``<out_base>.svg``; return their paths."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection, PatchCollection
    from matplotlib.patches import Polygon as MplPolygon

    # Chinese room and title labels are common in these drawings; without a
    # CJK face they render as boxes. Geometry is unaffected either way.
    matplotlib.rcParams["font.sans-serif"] = [
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK SC",
        "DejaVu Sans",
    ]
    matplotlib.rcParams["axes.unicode_minus"] = False

    base = Path(out_base)
    base.parent.mkdir(parents=True, exist_ok=True)

    lines: list[list[tuple[float, float]]] = []
    for start, end, _layer, _handle in segments:
        if len(lines) >= max_segments:
            break
        if region is not None and not (_clip(start, region) or _clip(end, region)):
            continue
        lines.append([start, end])

    patches = []
    for polygon in polygons:
        if region is not None and not polygon.intersects(
            _region_box(region)
        ):
            continue
        patch = MplPolygon(polygon.exterior.coords, closed=True)
        patches.append(patch)

    fig, ax = plt.subplots(figsize=(14, 10), dpi=140)
    if lines:
        ax.add_collection(LineCollection(lines, colors="#9aa3ad", linewidths=0.35, alpha=0.8))
    if patches:
        ax.add_collection(
            PatchCollection(patches, facecolor="#c0392b", edgecolor="#7b241c", alpha=0.45, linewidths=0.4)
        )

    if region is not None:
        ax.set_xlim(region[0], region[2])
        ax.set_ylim(region[1], region[3])
    elif patches or lines:
        ax.autoscale()
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_facecolor("#ffffff")
    ax.set_title(f"{title} — {len(patches)} wall areas", fontsize=11)
    ax.tick_params(labelsize=7)
    fig.tight_layout()

    png = base.with_suffix(".png")
    svg = base.with_suffix(".svg")
    fig.savefig(png, dpi=140, facecolor="white")
    fig.savefig(svg, facecolor="white")
    plt.close(fig)
    return {"png": str(png), "svg": str(svg)}


def _region_box(region: tuple[float, float, float, float]):
    from shapely.geometry import box

    return box(*region)


def drawable_segments(document: Any, *, layers: set[str] | None = None):
    from .document import iter_segments

    return iter_segments(document, layers=layers)
