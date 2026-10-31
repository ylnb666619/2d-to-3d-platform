"""Turn 2D architectural floor plans into reviewable 3D massing models."""

from __future__ import annotations

__version__ = "0.1.0"

from .document import DrawingReadError, open_document, read_document  # noqa: E402
from .layers import classify, classify_name, load_config  # noqa: E402
from .model import DocumentInfo, Units, WallResult, WallSegment  # noqa: E402
from .walls import ExtractOptions, extract, region_bounds  # noqa: E402

__all__ = [
    "__version__",
    "DrawingReadError",
    "open_document",
    "read_document",
    "classify",
    "classify_name",
    "load_config",
    "DocumentInfo",
    "Units",
    "WallResult",
    "WallSegment",
    "ExtractOptions",
    "extract",
    "region_bounds",
]
