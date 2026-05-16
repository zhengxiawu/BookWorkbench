"""Workspace project discovery for the local browser app."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List


PROJECT_MARKERS = ("book.spec.md", ".bookai/project.yaml")


def list_projects(workspace_root: str | Path) -> List[Dict[str, object]]:
    """Return BookWorkbench projects directly under ``workspace_root``.

    Discovery is intentionally shallow and marker-based so the empty app does
    not accidentally import design fixtures or unrelated repositories. A user
    must create or open an explicit workspace project before manuscript state is
    loaded.
    """

    workspace = Path(workspace_root).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    projects: List[Dict[str, object]] = []
    for candidate in sorted(path for path in workspace.iterdir() if path.is_dir()):
        if not all((candidate / marker).exists() for marker in PROJECT_MARKERS):
            continue
        projects.append(project_summary(candidate, workspace))
    return projects


def project_summary(project_root: str | Path, workspace_root: str | Path | None = None) -> Dict[str, object]:
    root = Path(project_root).resolve()
    workspace = Path(workspace_root).resolve() if workspace_root is not None else root.parent
    rel = root.relative_to(workspace).as_posix() if workspace in root.parents else root.name
    title = _project_yaml_value(root / ".bookai" / "project.yaml", "title") or _title_from_book_spec(root / "book.spec.md") or root.name
    slug = _project_yaml_value(root / ".bookai" / "project.yaml", "slug") or root.name
    chapters = sorted((root / "chapters").glob("*.md")) if (root / "chapters").exists() else []
    annotations = root / ".bookai" / "annotations.jsonl"
    annotation_count = 0
    if annotations.exists():
        annotation_count = sum(1 for line in annotations.read_text(encoding="utf-8").splitlines() if line.strip())
    return {
        "title": title,
        "slug": slug,
        "relativePath": rel,
        "root": root.as_posix(),
        "chapterCount": len(chapters),
        "annotationCount": annotation_count,
        "updatedAt": int(root.stat().st_mtime),
    }


def resolve_workspace_project(workspace_root: str | Path, relative_path: str) -> Path:
    workspace = Path(workspace_root).resolve()
    target = (workspace / relative_path).resolve()
    if target == workspace or workspace not in target.parents:
        raise ValueError("Project path escaped the workspace.")
    if not all((target / marker).exists() for marker in PROJECT_MARKERS):
        raise ValueError(f"Not a BookWorkbench project: {relative_path}")
    return target


def _project_yaml_value(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    prefix = f"{key}:"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(prefix):
            value = line.split(":", 1)[1].strip()
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
            return value or None
    return None


def _title_from_book_spec(path: Path) -> str | None:
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^#\s*(.+?)\s+Book SPEC\s*$", line.strip())
        if match:
            return match.group(1).strip()
    return None
