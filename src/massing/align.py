"""Align two floor plans by the overlap of their walls.

Every other datum can fail: axis bubbles may be missing from one of the
drawings, and a stair object is drawn differently in each (the ground floor of
a real 建施图 carries one flight where the upper floors carry the whole well, so
the stair centres do not correspond).

The walls do not lie. The same building drawn twice has the same walls, so the
translation that puts the most wall on top of wall is the right one. The search
is a plain cross-correlation of two wall rasters — fast, and it degrades
gracefully when a floor genuinely has a wing the other lacks: the shared part
still wins.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np


def _raster(
    polygons: Sequence[Any],
    origin: tuple[float, float],
    shape: tuple[int, int],
    resolution: float,
) -> np.ndarray:
    """Wall bodies as a boolean grid, in world coordinates."""
    from PIL import Image, ImageDraw

    image = Image.new("1", (shape[1], shape[0]), 0)
    draw = ImageDraw.Draw(image)
    for polygon in polygons:
        points = [
            ((x - origin[0]) / resolution, (y - origin[1]) / resolution)
            for x, y in polygon.exterior.coords
        ]
        if len(points) >= 3:
            draw.polygon(points, fill=1)
        for ring in polygon.interiors:
            holes = [((x - origin[0]) / resolution, (y - origin[1]) / resolution) for x, y in ring.coords]
            if len(holes) >= 3:
                draw.polygon(holes, fill=0)
    return np.asarray(image, dtype=np.float32)


def offset(
    reference: Sequence[Any],
    target: Sequence[Any],
    *,
    resolution: float = 100.0,
    max_pixels: int = 900,
) -> tuple[tuple[float, float], float] | None:
    """The translation that best puts ``target``'s walls onto ``reference``'s.

    Returns ``((dx, dy), score)`` where ``score`` is the overlap as a fraction
    of the smaller wall area, or ``None`` when there is nothing to compare.
    """
    if not reference or not target:
        return None

    boxes = [polygon.bounds for polygon in reference] + [polygon.bounds for polygon in target]
    minx = min(box[0] for box in boxes)
    miny = min(box[1] for box in boxes)
    maxx = max(box[2] for box in boxes)
    maxy = max(box[3] for box in boxes)

    # Coarsen rather than allocate an enormous grid for two plans drawn far
    # apart on one sheet.
    span = max(maxx - minx, maxy - miny)
    step = max(resolution, span / max_pixels)
    width = int(math.ceil((maxx - minx) / step)) + 3
    height = int(math.ceil((maxy - miny) / step)) + 3
    origin = (minx - step, miny - step)

    a = _raster(reference, origin, (height, width), step)
    b = _raster(target, origin, (height, width), step)
    area_a, area_b = a.sum(), b.sum()
    if area_a <= 0 or area_b <= 0:
        return None

    # Circular cross-correlation on a twice-padded grid keeps the peak exact
    # and avoids wrap-around between the two ends of the canvas.
    pad = (2 * height, 2 * width)
    fa = np.fft.rfft2(a, s=pad)
    fb = np.fft.rfft2(b, s=pad)
    corr = np.fft.irfft2(fa * np.conj(fb), s=pad)
    corr = np.fft.fftshift(corr)
    peak_y, peak_x = np.unravel_index(int(np.argmax(corr)), corr.shape)
    dy_cells = peak_y - pad[0] // 2
    dx_cells = peak_x - pad[1] // 2
    score = float(corr[peak_y, peak_x]) / float(min(area_a, area_b))
    return (dx_cells * step, dy_cells * step), score
