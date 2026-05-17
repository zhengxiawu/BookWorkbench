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


def _powerbook_status_overrides(root: Path) -> Dict[str, str]:
    import_path = root / ".bookai" / "powerbook-import.json"
    if not import_path.exists():
        return {}
    try:
        imported = json.loads(import_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    chapters = imported.get("chapters") if isinstance(imported, dict) else []
    overrides: Dict[str, str] = {}
    if not isinstance(chapters, list):
        return overrides
    for chapter in chapters:
        if not isinstance(chapter, dict):
            continue
        target = chapter.get("target")
        status = chapter.get("reviewStatus") or chapter.get("bookWorkbenchStatus")
        if isinstance(target, str) and isinstance(status, str) and status:
            overrides[target] = status
    return overrides


def _powerbook_guide_status_overrides(root: Path) -> Dict[str, str]:
    guide_path = root / ".bookai" / "powerbook-guide.json"
    if not guide_path.exists():
        return {}
    try:
        guide = json.loads(guide_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    target = guide.get("chapterTarget") if isinstance(guide, dict) else None
    return {target: "draft"} if isinstance(target, str) and target else {}


def _safe_project_file(root: Path, file_path: str) -> Path:
    project_root = root.resolve()
    full_path = (root / file_path).resolve()
    if project_root not in full_path.parents and full_path != project_root:
        raise ProjectLoadError(f"Project metadata references a path outside the project: {file_path}")
    return full_path


def safe_chapter_path(root: Path, file_path: str) -> Path:
    """Return an allowed real chapter path or raise ProjectLoadError.

    Runtime patching is intentionally limited to real Markdown files directly
    under ``chapters/``. Resolving the path here blocks both lexical traversal
    and symlink escapes before a preview/apply transaction can read or write.
    """

    if not isinstance(file_path, str):
        raise ProjectLoadError(f"Chapter path must be a string: {file_path!r}")
    candidate = root / file_path
    if candidate.is_symlink():
        raise ProjectLoadError(f"Chapter path must not be a symlink: {file_path}")
    chapter_path = _safe_project_file(root, file_path)
    chapters_root = (root / "chapters").resolve()
    if chapter_path.parent != chapters_root:
        raise ProjectLoadError(f"Chapter path must be a direct chapters/*.md file: {file_path}")
    if chapter_path.suffix != ".md":
        raise ProjectLoadError(f"Chapter path must be Markdown: {file_path}")
    if not chapter_path.is_file():
        raise ProjectLoadError(f"Chapter path must be a file: {file_path}")
    return chapter_path


def _chapter_files(root: Path, annotations: Iterable[Annotation], chapter_status: Dict[str, str]) -> List[Path]:
    # Deduplicate metadata references before calling ``safe_chapter_path``.
    # Large imported review sets commonly contain thousands of annotations
    # spread over the same few chapter files; resolving every annotation path
    # individually made dashboard/project open time scale with annotation
    # count. The security boundary remains the same because every unique
    # metadata path still goes through ``safe_chapter_path`` before indexing.
    files = set()
    referenced_paths = set(chapter_status)
    referenced_paths.update(annotation.file for annotation in annotations)
    for file_path in referenced_paths:
        try:
            files.add(safe_chapter_path(root, file_path))
        except ProjectLoadError:
            continue
    chapters_dir = root / "chapters"
    if chapters_dir.exists():
        files.update(file for file in chapters_dir.glob("*.md") if not file.is_symlink() and file.is_file())
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


def block_index_from_context(context: ProjectContext) -> Dict[str, Dict[str, Dict[str, int | str]]]:
    """Return the persisted sidecar block index for the current files.

    The index is a cache of anchors already embedded in ``chapters/*.md``. It
    must therefore be rebuilt from a freshly loaded ``ProjectContext`` after
    any accepted patch writes chapter text; otherwise later annotation remap
    and beforeHash checks can compare against stale sidecar data.
    """

    return {
        file_path: {
            block_id: {
                "hash": block.before_hash,
                "startLine": block.start_line,
                "endLine": block.end_line,
            }
            for block_id, block in sorted(blocks.items())
        }
        for file_path, blocks in sorted(context.blocks.items())
    }


def write_block_index(context: ProjectContext) -> Path:
    """Rewrite ``.bookai/block-index.json`` from the current context."""

    path = context.root / ".bookai" / "block-index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(block_index_from_context(context), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def markdown_title(root: Path, file_path: str) -> str:
    """Return the human chapter title from a Markdown chapter file."""

    chapter_path = safe_chapter_path(root, file_path)
    text = chapter_path.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or Path(file_path).stem
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped == "---" or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        if key.strip() == "title":
            title = value.strip().strip('"').strip("'")
            if title:
                return title
    return Path(file_path).stem


def manuscript_word_count(text: str) -> int:
    """Count visible manuscript characters for Chinese-first dashboard stats."""

    without_anchors = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return len(re.sub(r"\s+", "", without_anchors))


def chapter_summaries(context: ProjectContext) -> Dict[str, Dict[str, int | str]]:
    """Return title, display status, normalized edit status, and word count."""

    summaries: Dict[str, Dict[str, int | str]] = {}
    for file_path, blocks in sorted(context.blocks.items()):
        body = "\n".join(block.text for block in blocks.values())
        summaries[file_path] = {
            "title": markdown_title(context.root, file_path),
            "status": context.chapter_status.get(file_path, "draft"),
            "editStatus": context.status_for_file(file_path),
            "blockCount": len(blocks),
            "wordCount": manuscript_word_count(body),
        }
    return summaries


def load_project(root: str | Path) -> ProjectContext:
    project_root = Path(root).resolve()
    if not project_root.exists():
        raise ProjectLoadError(f"Project path does not exist: {project_root}")

    status_path = project_root / ".bookai" / "chapter-status.yaml"
    annotations = _load_annotations(project_root / ".bookai" / "annotations.jsonl")
    chapter_status = load_chapter_status(_read_optional(status_path))
    chapter_status.update(_powerbook_guide_status_overrides(project_root))
    for file_path, status in _powerbook_status_overrides(project_root).items():
        if chapter_status.get(file_path, "unreviewed") == "unreviewed":
            chapter_status[file_path] = status
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
