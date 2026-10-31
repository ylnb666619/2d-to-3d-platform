"""A sheet that is only pixels: find the rooms, the strokes, and the scale.

A 建施图 plotted to PDF from ZWCAD keeps its vectors and its text. A sheet that
was scanned, or printed to an image, keeps neither: there is no layer to ask,
no ``SWR_WALL`` to unpack. This module reads the second kind.

The one thing a line drawing states unambiguously is where it is white. A room
is a hole in the ink, the sheet margin is the sea outside the frame, and a
letter is a pinprick -- so the rooms are found by flooding the white and
keeping the components that are the size of rooms. That is not a guess about
how someone drew a wall; it is a measurement of what they closed.

Scale is the honest weak point. The page says how wide it is in millimetres
(the PDF knows), the raster says how wide it is in pixels, and the drawing
says 1:100 in a corner nobody has OCR'd yet -- so the ratio is offered as a
setting with 1:100 as its default, and every area is recomputed when it
changes rather than being believed.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage

# A sheet is read at half its raster width: 1 px is then about 0.17 mm of
# paper at 300 dpi, which is finer than a wall line is thick.
WORKING_SCALE = 0.5

MM_PER_PT = 25.4 / 72.0

# A room in a school is somewhere between a store cupboard and a hall. The
# bounds are in m², so they are only applied once the scale is known.
MIN_ROOM_M2 = 6.0
MAX_ROOM_M2 = 400.0


@dataclass
class SheetReading:
    page: int
    width: int
    height: int
    page_mm: tuple[float, float]
    drawing_scale: float
    working_scale: float = WORKING_SCALE
    rooms: list[dict[str, Any]] = field(default_factory=list)
    strokes: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def mm_per_px(self) -> float:
        """Millimetres of building per pixel of the working image."""
        return self.page_mm[0] / max(1, self.width) * self.drawing_scale

    def with_scale(self, drawing_scale: float) -> "SheetReading":
        """Recompute every area, and drop what is not a room once measured.

        A 5,000 m² hole is the space between the frame and the plan; a 2 m² hole
        is the gap inside a dimension chain. Neither is a room, and both are
        only visible once pixels have become metres.
        """
        self.drawing_scale = drawing_scale
        kept = []
        for room in self.rooms:
            x0, y0, x1, y1 = room["bounds"]
            area = (x1 - x0) * (y1 - y0) * self.mm_per_px ** 2 / 1e6
            room["areaM2"] = round(area, 1)
            if MIN_ROOM_M2 <= area <= MAX_ROOM_M2 and room.get("fill", 1.0) >= 0.5:
                kept.append(room)
        self.rooms = kept
        self.stats["rooms"] = len(kept)
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "size": [self.width, self.height],
            "pageMm": [round(self.page_mm[0], 1), round(self.page_mm[1], 1)],
            "drawingScale": round(self.drawing_scale, 1),
            "mmPerPx": round(self.mm_per_px, 3),
            "rooms": self.rooms,
            "strokes": self.strokes[:400],
            "stats": self.stats,
        }


def load_page(path: str, index: int = 0, *, scale: float = WORKING_SCALE):
    """The page as a working image, its own size in mm, and the sheet's size."""
    import pymupdf

    document = pymupdf.open(path)
    page = document[index]
    images = page.get_images(full=True)
    if images:
        raw = document.extract_image(images[0][0])["image"]
        full = Image.open(io.BytesIO(raw))
    else:
        pixmap = page.get_pixmap(dpi=200)
        full = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    if full.mode != "L":
        full = full.convert("L")
    # A sheet scanned portrait and placed on a landscape page is read sideways.
    if full.height > full.width:
        full = full.rotate(90, expand=True)
    working = full.resize(
        (max(1, int(full.width * scale)), max(1, int(full.height * scale))), Image.BILINEAR
    )
    rect = page.rect
    page_mm = (max(rect.width, rect.height) * MM_PER_PT, min(rect.width, rect.height) * MM_PER_PT)
    return working, page_mm, full


def _runs(values: np.ndarray) -> list[tuple[int, int]]:
    if not values.any():
        return []
    padded = np.concatenate(([False], values, [False])).astype(np.int8)
    edges = np.flatnonzero(np.diff(padded))
    return list(zip(edges[0::2], edges[1::2]))


def _long_strokes(mask: np.ndarray, axis: str, *, min_length: int) -> list[dict[str, Any]]:
    """Long straight runs of ink, merged across their thickness."""
    work = mask if axis == "h" else mask.T
    height = work.shape[0]
    open_runs: dict[tuple[int, int], int] = {}
    strokes: list[dict[str, Any]] = []

    for row in range(height):
        current = set()
        for x0, x1 in _runs(work[row]):
            if x1 - x0 >= min_length:
                current.add((int(x0), int(x1)))
        for span in list(open_runs):
            if span not in current:
                start = open_runs.pop(span)
                strokes.append(_stroke(axis, span, start, row - 1))
        for span in current:
            open_runs.setdefault(span, row)
    for span, start in open_runs.items():
        strokes.append(_stroke(axis, span, start, height - 1))
    return strokes


def _stroke(axis: str, span: tuple[int, int], y0: int, y1: int) -> dict[str, Any]:
    thickness = y1 - y0 + 1
    if axis == "h":
        bounds = [span[0], y0, span[1], y1]
    else:
        bounds = [y0, span[0], y1, span[1]]
    return {
        "axis": axis,
        "bounds": bounds,
        "length": int(max(span[1] - span[0], y1 - y0 + 1)),
        "thickness": int(thickness),
    }


def _side_evidence(mask: np.ndarray, bounds: list[int], *, depth: int = 12, samples: int = 24) -> list[bool]:
    """Which of the four sides of a box is a wall rather than a stray line.

    A room is bounded by walls, and in a drawn plan a wall is two lines or a
    filled band, never one hairline. A dimension chain, a leader, or the gap
    inside a hatch cell closes a box with hairlines -- which is why so many
    boxes look like rooms until the sides are looked at one at a time.
    """
    height, width = mask.shape
    x0, y0, x1, y1 = bounds
    sides: list[bool] = []

    def evidence(profile: np.ndarray) -> bool:
        if not len(profile):
            return False
        runs = int(np.count_nonzero(np.diff(np.concatenate(([0], profile.astype(np.int8), [0]))) == 1))
        return bool(profile.sum() >= 3 or runs >= 2)

    for name in ("top", "bottom", "left", "right"):
        hits = 0
        total = 0
        for step in range(samples):
            if name in ("top", "bottom"):
                position = int(x0 + (x1 - x0) * (step + 0.5) / samples)
                if not (0 <= position < width):
                    continue
                centre = y0 if name == "top" else y1
                profile = mask[max(0, centre - depth):min(height, centre + depth + 1), position]
            else:
                position = int(y0 + (y1 - y0) * (step + 0.5) / samples)
                if not (0 <= position < height):
                    continue
                centre = x0 if name == "left" else x1
                profile = mask[position, max(0, centre - depth):min(width, centre + depth + 1)]
            total += 1
            hits += evidence(profile)
        sides.append(total > 0 and hits >= total * 0.55)
    return sides


def find_rooms(mask: np.ndarray, *, min_px: int, max_share: float = 0.25) -> list[dict[str, Any]]:
    """Rooms are the holes in the linework: flood the white, keep the rooms."""
    height, width = mask.shape
    closed = ndimage.binary_dilation(mask, np.ones((3, 3)), iterations=1)
    labels, count = ndimage.label(~closed)
    if not count:
        return []
    sizes = ndimage.sum(~closed, labels, range(1, count + 1))
    border = set(
        np.unique(
            np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])
        ).tolist()
    )
    limit = height * width * max_share
    rooms: list[dict[str, Any]] = []
    for index in np.argsort(sizes)[::-1]:
        label = int(index) + 1
        if label in border:
            continue
        size = float(sizes[index])
        if size < min_px:
            break
        if size > limit:
            continue
        ys, xs = np.where(labels == label)
        if not len(xs):
            continue
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        box_area = max(1, (x1 - x0 + 1) * (y1 - y0 + 1))
        sides = _side_evidence(mask, [x0, y0, x1, y1])
        if sum(sides) < 3:
            # A box held together by hairlines on two sides is a gap between
            # dimension lines, not a room.
            continue
        rooms.append(
            {
                "bounds": [x0, y0, x1, y1],
                "pixels": int(size),
                "fill": round(size / box_area, 2),
                "walls": int(sum(sides)),
                "name": "",
            }
        )
        if len(rooms) >= 300:
            break
    rooms.sort(key=lambda room: (room["bounds"][1], room["bounds"][0]))
    return rooms


def read_sheet(
    path: str,
    index: int = 0,
    *,
    drawing_scale: float = 100.0,
    min_room_px: int = 800,
    min_stroke: int = 120,
    scale: float = WORKING_SCALE,
) -> SheetReading:
    working, page_mm, full = load_page(path, index, scale=scale)
    array = np.array(working)
    dark = array < 160

    strokes = _long_strokes(dark, "h", min_length=min_stroke)
    strokes += _long_strokes(dark, "v", min_length=min_stroke)
    rooms = find_rooms(dark, min_px=min_room_px)

    reading = SheetReading(
        page=index,
        width=array.shape[1],
        height=array.shape[0],
        page_mm=page_mm,
        drawing_scale=drawing_scale,
        working_scale=scale,
        rooms=rooms,
        strokes=strokes,
        stats={
            "ink": round(float(dark.mean()) * 100, 2),
            "horizontalStrokes": sum(1 for stroke in strokes if stroke["axis"] == "h"),
            "verticalStrokes": sum(1 for stroke in strokes if stroke["axis"] == "v"),
            "rooms": len(rooms),
            "workingSize": [array.shape[1], array.shape[0]],
            "rasterSize": list(full.size),
            "minRoomPx": min_room_px,
        },
    )
    return reading.with_scale(drawing_scale)


def render_overlay(reading: SheetReading, path: str, out_png: str) -> str:
    """The sheet with what was read drawn on top, so it can be checked."""
    from PIL import ImageDraw

    working, _page_mm, _full = load_page(path, reading.page, scale=reading.working_scale)
    image = working.convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    for stroke in reading.strokes:
        draw.rectangle(stroke["bounds"], outline=(220, 38, 38, 170))
    for room in reading.rooms:
        x0, y0, x1, y1 = room["bounds"]
        draw.rectangle([x0, y0, x1, y1], fill=(37, 99, 235, 45), outline=(37, 99, 235, 220))
    image.save(out_png)
    return out_png


def build_page(
    path: str,
    root,
    *,
    drawing_scale: float = 100.0,
    on_page=None,
) -> dict:
    """Read every page of a PDF and write the page that shows the reading."""
    import html as html_mod
    import json
    from pathlib import Path

    import pymupdf

    document = pymupdf.open(path)
    count = document.page_count
    folder = Path(root) / "pdf"
    folder.mkdir(parents=True, exist_ok=True)

    pages = []
    for index in range(count):
        reading = read_sheet(path, index, drawing_scale=drawing_scale)
        png = folder / f"page-{index + 1}.png"
        render_overlay(reading, path, str(png))
        data = reading.to_dict()
        data["image"] = f"pdf/{png.name}"
        data["title"] = f"{Path(path).name} 第 {index + 1} 页"
        pages.append(data)
        if on_page is not None:
            on_page(index + 1, count, reading)

    template = Path(__file__).with_name("pdf_page.html").read_text(encoding="utf-8")
    document_html = template.replace(
        "__TITLE__", html_mod.escape(Path(path).name)
    ).replace("__PAGES__", json.dumps(pages, ensure_ascii=False))
    out = Path(root) / "pdf.html"
    out.write_text(document_html, encoding="utf-8")
    return {
        "ok": True,
        "path": str(path),
        "pages": count,
        "rooms": sum(len(page["rooms"]) for page in pages),
        "html": str(out),
        "url": "/pdf.html",
    }
