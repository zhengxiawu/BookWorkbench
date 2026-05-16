"""Project discussion sidecar helpers for BookWorkbench."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

from .audit import utc_now


def discussions_path(project_root: str | Path) -> Path:
    return Path(project_root) / ".bookai" / "discussions.jsonl"


def list_discussions(project_root: str | Path) -> List[Dict[str, Any]]:
    path = discussions_path(project_root)
    if not path.exists():
        return []
    items: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            items.append(payload)
    return items


def next_discussion_id(items: Iterable[Mapping[str, Any]]) -> str:
    max_seen = 0
    for item in items:
        item_id = item.get("id")
        if isinstance(item_id, str) and item_id.startswith("DS-") and item_id[3:].isdigit():
            max_seen = max(max_seen, int(item_id[3:]))
    return f"DS-{max_seen + 1:03d}"


def append_discussion(
    project_root: str | Path,
    *,
    text: str,
    file_path: str | None = None,
    block_id: str | None = None,
    role: str = "author",
) -> Dict[str, Any]:
    body = text.strip()
    if not body:
        raise ValueError("discussion text is required.")
    existing = list_discussions(project_root)
    item = {
        "id": next_discussion_id(existing),
        "type": "discussion",
        "role": role.strip() or "author",
        "text": body,
        "file": file_path or "",
        "blockId": block_id or "",
        "createdAt": utc_now(),
        "status": "open",
    }
    path = discussions_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        if path.exists() and path.stat().st_size > 0:
            handle.write("\n")
        handle.write(json.dumps(item, ensure_ascii=False))
    return item
