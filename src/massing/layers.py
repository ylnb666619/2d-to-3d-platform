"""Map layer names to architectural roles.

Every office names its layers differently, so this is configuration, not code.
The classifier reports the rule that fired and, more importantly, the layers it
could not place: on a real drawing that list is the thing a person must review.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .model import DocumentInfo, LayerInfo

_SEPARATORS = re.compile(r"[^0-9a-z一-鿿]+")
DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "layer-roles.default.json"

ROLES = ("wall", "column", "window", "door", "axis", "dim", "text", "hatch", "frame")


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load a role map; ``None`` reads the packaged default."""
    source = Path(path) if path else DEFAULT_CONFIG
    if not source.is_file():
        raise FileNotFoundError(f"找不到图层配置：{source}")
    data = json.loads(source.read_text(encoding="utf-8"))
    roles = data.get("roles")
    if not isinstance(roles, dict):
        raise ValueError(f"图层配置缺少 roles 对象：{source}")
    priority = data.get("priority") or list(roles)
    unknown = [role for role in priority if role not in roles]
    if unknown:
        raise ValueError(f"priority 里的角色没有定义：{', '.join(unknown)}")
    return {"roles": roles, "priority": priority, "source": str(source)}


def _tokens(name: str) -> list[str]:
    return [token for token in _SEPARATORS.split(name.strip().lower()) if token]


def classify_name(name: str, config: dict[str, Any]) -> tuple[str, str, str]:
    """Return ``(role, pattern, confidence)`` for one layer name."""
    lowered = name.strip().lower()
    if not lowered:
        return "unknown", "", "none"
    tokens = _tokens(name)

    # 1. Exact name first: a layer literally called "WALL" should never be
    #    beaten by a substring rule from a lower-priority role.
    for role in config["priority"]:
        for pattern in config["roles"][role]:
            if lowered == pattern.lower():
                return role, pattern, "exact"
    # 2. Whole token, which handles "A-WALL-INT" and "WALL_200".
    for role in config["priority"]:
        for pattern in config["roles"][role]:
            if pattern.lower() in tokens:
                return role, pattern, "token"
    # 3. Substring, which is what CJK names usually need ("外墙填充").
    for role in config["priority"]:
        for pattern in config["roles"][role]:
            if pattern.lower() in lowered:
                return role, pattern, "substring"
    return "unknown", "", "none"


def classify(document: DocumentInfo, config: dict[str, Any]) -> list[LayerInfo]:
    """Annotate every layer in place and return the layers, sorted by size."""
    for layer in document.layers:
        role, pattern, confidence = classify_name(layer.name, config)
        layer.role = role
        layer.rule = pattern
        layer.confidence = confidence
    document.layers.sort(key=lambda item: (item.role == "unknown", -item.count, item.name))
    return document.layers


def unmatched(document: DocumentInfo) -> list[LayerInfo]:
    return [layer for layer in document.layers if layer.role == "unknown" and layer.count > 0]


def layers_for(document: DocumentInfo, *roles: str) -> list[str]:
    wanted = set(roles)
    return [layer.name for layer in document.layers if layer.role in wanted]


def summary(document: DocumentInfo) -> dict[str, Any]:
    by_role: dict[str, dict[str, Any]] = {}
    for layer in document.layers:
        bucket = by_role.setdefault(layer.role, {"layers": [], "entities": 0})
        bucket["layers"].append(layer.name)
        bucket["entities"] += layer.count
    return {
        "byRole": {role: by_role[role] for role in sorted(by_role)},
        "unmatched": [layer.name for layer in unmatched(document)],
    }
