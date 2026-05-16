"""Project loading and Markdown block indexing."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Iterable, List

from .models import Annotation, MarkdownBlock, ProjectContext, Rule
from .yaml_lite import load_chapter_status, load_rules

ANCHOR_RE = re.compile(r"^<!--\s*mw:block\s+id=(?P<id>[^\s]+)\s+hash=(?P<hash>[^\s]+)\s*-->\s*$")


class ProjectLoadError(RuntimeError):
    pass


def _read_optional(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _load_annotations(path: Path) -> List[Annotation]:
    if not path.exists():
        return []
    annotations: List[Annotation] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProjectLoadError(f"Invalid JSON in {path}:{line_no}: {exc}") from exc
        target = raw.get("target", {})
        body = raw.get("body", {})
        metadata = raw.get("metadata", {})
        file_path = raw.get("file") or target.get("file")
        block_id = target.get("blockId")
        if not raw.get("id") or not file_path or not block_id:
            raise ProjectLoadError(f"Annotation at {path}:{line_no} is missing id/file/blockId")
        annotations.append(
            Annotation(
                id=raw["id"],
                file=file_path,
                block_id=block_id,
                text=body.get("text", ""),
                annotation_type=body.get("type", body.get("kind", "other")),
                priority=body.get("priority", "medium"),
                status=metadata.get("status", "open"),
                before_hash=target.get("beforeHash"),
                selected_text=target.get("selectedText"),
            )
        )
    return annotations


def _load_rules(path: Path) -> List[Rule]:
    if not path.exists():
        return []
    return [
        Rule(
            id=str(raw.get("id", "")),
            type=str(raw.get("type", "other")),
            text=str(raw.get("text", "")),
            source_annotations=[str(item) for item in raw.get("source_annotations", [])],
            priority=str(raw.get("priority", "medium")),
            apply_to=[str(item) for item in raw.get("apply_to", [])],
            exclude=[str(item) for item in raw.get("exclude", [])],
            status=str(raw.get("status", "active")),
        )
        for raw in load_rules(path.read_text(encoding="utf-8"))
    ]


def _chapter_files(root: Path, annotations: Iterable[Annotation], chapter_status: Dict[str, str]) -> List[Path]:
    files = {root / file_path for file_path in chapter_status}
    files.update(root / annotation.file for annotation in annotations)
    chapters_dir = root / "chapters"
    if chapters_dir.exists():
        files.update(chapters_dir.glob("*.md"))
    return sorted(file for file in files if file.exists())


def index_markdown_blocks(root: Path, file_path: str) -> Dict[str, MarkdownBlock]:
    full_path = root / file_path
    lines = full_path.read_text(encoding="utf-8").splitlines()
    blocks: Dict[str, MarkdownBlock] = {}
    current_id = None
    current_hash = None
    current_anchor = None
    anchor_line = 0
    text_start = 0
    text_lines: List[str] = []

    def flush(end_line: int) -> None:
        nonlocal current_id, current_hash, current_anchor, anchor_line, text_start, text_lines
        if not current_id:
            return
        while text_lines and text_lines[-1] == "":
            text_lines.pop()
        blocks[current_id] = MarkdownBlock(
            id=current_id,
            file=file_path,
            anchor=current_anchor or "",
            before_hash=current_hash or "",
            text="\n".join(text_lines),
            start_line=text_start,
            end_line=end_line,
            anchor_line=anchor_line,
        )

    for idx, line in enumerate(lines, start=1):
        match = ANCHOR_RE.match(line)
        if match:
            flush(idx - 1)
            current_id = match.group("id")
            current_hash = match.group("hash")
            current_anchor = line
            anchor_line = idx
            text_start = idx + 1
            text_lines = []
        elif current_id:
            text_lines.append(line)
    flush(len(lines))
    return blocks


def load_project(root: str | Path) -> ProjectContext:
    project_root = Path(root).resolve()
    if not project_root.exists():
        raise ProjectLoadError(f"Project path does not exist: {project_root}")

    status_path = project_root / ".bookai" / "chapter-status.yaml"
    annotations = _load_annotations(project_root / ".bookai" / "annotations.jsonl")
    chapter_status = load_chapter_status(_read_optional(status_path))
    rules = _load_rules(project_root / "rules.yaml")

    blocks: Dict[str, Dict[str, MarkdownBlock]] = {}
    for chapter_file in _chapter_files(project_root, annotations, chapter_status):
        rel = chapter_file.relative_to(project_root).as_posix()
        blocks[rel] = index_markdown_blocks(project_root, rel)

    return ProjectContext(
        root=project_root,
        book_spec=_read_optional(project_root / "book.spec.md"),
        style_guide=_read_optional(project_root / "style-guide.md"),
        rules=rules,
        chapter_status=chapter_status,
        annotations=annotations,
        blocks=blocks,
    )
