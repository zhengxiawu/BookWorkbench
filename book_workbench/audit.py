"""JSONL audit logging for Runtime-owned operations."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class AuditLog:
    def __init__(self, project_root: str | Path, *, path: str | Path | None = None) -> None:
        self.project_root = Path(project_root)
        self.path = Path(path) if path is not None else self.project_root / ".bookai" / "audit-log.jsonl"

    def append(self, event: Dict[str, object]) -> Dict[str, object]:
        payload = {"timestamp": utc_now(), **event}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        return payload

    def read(self) -> List[Dict[str, object]]:
        if not self.path.exists():
            return []
        events: List[Dict[str, object]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
        return events
