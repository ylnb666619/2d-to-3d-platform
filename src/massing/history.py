"""The drawings this machine has read, most recent first.

The page's home screen is only useful if it remembers what you were working
on, and the drawing itself is the state -- a path, when it was read, and what
came out. One small JSON file beside the page, rewritten after every read.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

HISTORY = "history.json"
MODELS = "models.json"
KEEP = 12
KEEP_MODELS = 20


def _read(root: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads((root / HISTORY).read_text(encoding="utf-8"))
        return [entry for entry in data if isinstance(entry, dict)]
    except Exception:  # noqa: BLE001 - a missing or broken history is an empty one
        return []


def recent(root: Path, limit: int = KEEP) -> list[dict[str, Any]]:
    """Recent drawings, newest first, with the files that are still there."""
    entries = []
    for entry in _read(root):
        source = str(entry.get("source") or "")
        if not source:
            continue
        entry["exists"] = Path(source).exists()
        entries.append(entry)
    entries.sort(key=lambda item: str(item.get("at") or ""), reverse=True)
    return entries[:limit]


def _read_models(root: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads((root / MODELS).read_text(encoding="utf-8"))
        return [entry for entry in data if isinstance(entry, dict)]
    except Exception:  # noqa: BLE001
        return []


def models(root: Path, limit: int = KEEP_MODELS) -> list[dict[str, Any]]:
    """Models built here before: one sentence, an export, whatever wrote a STEP."""
    entries = []
    for entry in _read_models(root):
        path = str(entry.get("file") or "")
        if not path:
            continue
        entry["exists"] = Path(path).exists()
        if entry["exists"]:
            try:
                entry["bytes"] = Path(path).stat().st_size
            except OSError:
                entry["bytes"] = 0
        # Older entries predate the browser copy: rebuild it from the sentence,
        # which costs nothing, so the app's own viewer can still draw them.
        if not entry.get("scene") and entry.get("kind") == "generated" and entry.get("text"):
            try:
                from .preset import parse, scene

                scene_file = Path(path).with_suffix(".scene.json")
                if not scene_file.exists():
                    scene_file.write_text(
                        json.dumps(scene(parse(str(entry["text"]))), ensure_ascii=False),
                        encoding="utf-8",
                    )
                entry["scene"] = str(scene_file)
            except Exception:  # noqa: BLE001 - a model without a view is still a model
                pass
        entries.append(entry)
    entries.sort(key=lambda item: str(item.get("at") or ""), reverse=True)
    return entries[:limit]


def record_model(root: Path, entry: dict[str, Any]) -> None:
    """Remember a generated or exported model."""
    entries = [item for item in _read_models(root) if item.get("file") != entry.get("file")]
    entry.setdefault("at", datetime.now().isoformat(timespec="seconds"))
    entries.insert(0, entry)
    try:
        (root / MODELS).write_text(
            json.dumps(entries[:KEEP_MODELS], ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        pass

def record(
    root: Path,
    source: str,
    ground: str = "",
    *,
    summary: dict[str, Any] | None = None,
) -> None:
    """Remember a read. Same drawing twice moves it up, it does not duplicate."""
    entries = [entry for entry in _read(root) if entry.get("source") != source]
    path = Path(source)
    entry: dict[str, Any] = {
        "source": source,
        "ground": ground,
        "name": path.name,
        "at": datetime.now().isoformat(timespec="seconds"),
        "revision": 0,
    }
    try:
        stat = path.stat()
        entry["size"] = stat.st_size
        entry["revision"] = int(stat.st_mtime)
    except OSError:
        entry["size"] = 0
    if summary:
        entry.update({key: value for key, value in summary.items() if key not in entry})
    entries.insert(0, entry)
    try:
        (root / HISTORY).write_text(
            json.dumps(entries[:KEEP], ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        pass
