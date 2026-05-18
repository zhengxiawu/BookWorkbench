"""Import a read-only PowerBook writing project into BookWorkbench format."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .git_service import commit_all, ensure_repo, run_git
from .patch_engine import current_block_hash
from .project import load_project, write_block_index
from .project_creator import PROJECT_SKILL_FILES, ProjectCreationError, slugify
from .powerbook_memory import write_powerbook_memory


class PowerBookImportError(RuntimeError):
    pass


NOTE_MARKERS = ("AUTHOR-NOTE", "AuthorNote", "AuhorNote")


STATUS_MAP = {
    "draft": "draft",
    "annotated": "annotated",
    "briefed": "briefed",
    "revised": "revised",
    "locked": "locked",
}

EDIT_STATUS_MAP = {
    "draft": "draft",
    "annotated": "unreviewed",
    "briefed": "unreviewed",
    "revised": "unreviewed",
    "locked": "locked",
}


POWERBOOK_RULES = [
    {
        "id": "PB-001",
        "type": "workflow",
        "text": "默认一次只生成或修订一个完整章节，不把章节写成等待作者决定的问题清单。",
        "source_annotations": [],
        "priority": "high",
        "apply_to": ["draft", "unreviewed"],
        "exclude": ["reviewed", "locked"],
        "status": "active",
    },
    {
        "id": "PB-002",
        "type": "safety",
        "text": "有 AUTHOR-NOTE 的章节进入批注保护状态；批量改写必须跳过，除非作者明确点名修订该章。",
        "source_annotations": [],
        "priority": "high",
        "apply_to": ["draft", "unreviewed"],
        "exclude": ["reviewed", "locked"],
        "status": "active",
    },
    {
        "id": "PB-003",
        "type": "style",
        "text": "章节按具体事情、问题浮现、概念抽象、机制拆解、材料支撑、中国语境、反方与收束推进。",
        "source_annotations": [],
        "priority": "high",
        "apply_to": ["draft", "unreviewed"],
        "exclude": ["reviewed", "locked"],
        "status": "active",
    },
    {
        "id": "PB-004",
        "type": "style",
        "text": "抽象术语第一次出现时先用白话解释动作、成本、选择和后果，再给概念名。",
        "source_annotations": [],
        "priority": "high",
        "apply_to": ["draft", "unreviewed"],
        "exclude": ["reviewed", "locked"],
        "status": "active",
    },
    {
        "id": "PB-005",
        "type": "style",
        "text": "中国语境优先从历史路径、财政、组织、问责、家庭风险和资源约束解释，避免民族性或文化宿命论。",
        "source_annotations": [],
        "priority": "high",
        "apply_to": ["draft", "unreviewed"],
        "exclude": ["reviewed", "locked"],
        "status": "active",
    },
    {
        "id": "PB-006",
        "type": "fact",
        "text": "事实性判断不能编造引用、年份、数据、页码或政策细节；待查证内容进入 claims 证据登记。",
        "source_annotations": [],
        "priority": "high",
        "apply_to": ["draft", "unreviewed"],
        "exclude": ["reviewed", "locked"],
        "status": "active",
    },
]


def import_powerbook_project(
    source_root: str | Path,
    workspace_root: str | Path,
    *,
    slug: str = "powerbook",
    title: str | None = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Copy a PowerBook repo into a runnable BookWorkbench project.

    The source is treated as immutable. All generated anchors, sidecar files,
    and metadata are written only under ``workspace_root / slug``.
    """

    source = Path(source_root).resolve()
    workspace = Path(workspace_root).resolve()
    _validate_powerbook_source(source)
    project_slug = slugify(slug)
    target = (workspace / project_slug).resolve()
    if target == workspace or workspace not in target.parents:
        raise PowerBookImportError("Import target escaped the workspace.")
    if target.exists():
        if not overwrite:
            raise PowerBookImportError(f"Import target already exists: {target}")
        shutil.rmtree(target)

    workspace.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True)

    chapters_dir = target / "chapters"
    bookai_dir = target / ".bookai"
    chapters_dir.mkdir()
    bookai_dir.mkdir()

    source_hashes = _source_hashes(source)
    imported_chapters: List[Dict[str, Any]] = []
    annotations: List[Dict[str, Any]] = []
    status_lines = ["chapters:"]

    for chapter_path in sorted((source / "book" / "chapters").glob("ch*.md")):
        if chapter_path.name == "README.md":
            continue
        converted, metadata, chapter_annotations = _convert_chapter(chapter_path)
        relative = f"chapters/{chapter_path.name}"
        destination = target / relative
        destination.write_text(converted, encoding="utf-8")
        status_lines.extend([f"  {relative}:", f"    status: {metadata['bookWorkbenchStatus']}"])
        imported_chapters.append({"source": chapter_path.relative_to(source).as_posix(), "target": relative, **metadata})
        annotations.extend(chapter_annotations)

    if not imported_chapters:
        raise PowerBookImportError("No PowerBook chapters were found under book/chapters.")

    _copy_support_files(source, target)
    _write_project_files(source, target, title=title)
    (bookai_dir / "project.yaml").write_text(
        f"title: {title or _book_title(source)}\nslug: {project_slug}\nversion: 1\nsource: PowerBook\n",
        encoding="utf-8",
    )
    (bookai_dir / "chapter-status.yaml").write_text("\n".join(status_lines) + "\n", encoding="utf-8")
    (bookai_dir / "annotations.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in annotations),
        encoding="utf-8",
    )
    (bookai_dir / "discussions.jsonl").write_text(_import_discussions(source), encoding="utf-8")
    (bookai_dir / "powerbook-import.json").write_text(
        json.dumps(
            {
                "sourceRoot": source.as_posix(),
                "importedAt": datetime.now(timezone.utc).isoformat(),
                "sourceFileCount": len(source_hashes),
                "sourceTreeHash": _tree_hash(source_hashes),
                "chapters": imported_chapters,
                "annotationCount": len(annotations),
                "statusMapping": STATUS_MAP,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (bookai_dir / "source-hashes.sha256").write_text(
        "".join(f"{digest}  {path}\n" for path, digest in source_hashes),
        encoding="utf-8",
    )
    memory = write_powerbook_memory(target)
    for relative_path, content in PROJECT_SKILL_FILES.items():
        path = target / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    write_block_index(load_project(target))
    baseline_commit = _create_import_baseline_commit(target, source)
    return {
        "root": target.as_posix(),
        "sourceRoot": source.as_posix(),
        "slug": project_slug,
        "chapterCount": len(imported_chapters),
        "annotationCount": len(annotations),
        "sourceTreeHash": _tree_hash(source_hashes),
        "memoryArtifactCount": len(memory.get("artifacts", [])),
        "baselineCommitCreated": bool(baseline_commit),
        "baselineCommit": baseline_commit,
    }


def _create_import_baseline_commit(target: Path, source: Path) -> str:
    """Initialize the imported copy as a clean rollback baseline.

    PowerBook is imported from a read-only source tree into an editable local
    workspace.  The first Git commit must therefore represent exactly that
    imported state; later accepted PatchProposals can then be one-commit
    revisions instead of accidentally bundling the whole copied project.
    """

    ensure_repo(target)
    message = (
        "Import PowerBook baseline\n\n"
        "BookWorkbench copied the read-only PowerBook source into a local editable workspace "
        "and generated anchors, sidecars, and project-local Codex skills.\n\n"
        "Constraint: Source PowerBook tree remains read-only\n"
        "Confidence: high\n"
        "Scope-risk: narrow\n"
        "Directive: Keep subsequent accepted patches as separate commits after this baseline\n"
        "Tested: import_powerbook_project baseline git regression\n"
        f"Related: source={source.as_posix()}\n"
    )
    commit_all(target, message, name="BookWorkbench Importer", email="importer@bookworkbench.local")
    result = run_git(["rev-parse", "--verify", "HEAD"], target)
    return result.stdout.strip() if result.returncode == 0 else ""


def _validate_powerbook_source(source: Path) -> None:
    required = [
        "AGENTS.md",
        "WORKFLOW.md",
        "book/outline.md",
        "book/chapters",
        "theory/core_definitions.md",
        "claims/claim_register.yaml",
    ]
    missing = [path for path in required if not (source / path).exists()]
    if missing:
        raise PowerBookImportError(f"Not a PowerBook source; missing: {', '.join(missing)}")


def _source_hashes(source: Path) -> List[Tuple[str, str]]:
    rows: List[Tuple[str, str]] = []
    for path in sorted(item for item in source.rglob("*") if item.is_file() and item.name != ".DS_Store"):
        rows.append((path.relative_to(source).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    return rows


def _tree_hash(rows: Iterable[Tuple[str, str]]) -> str:
    payload = "".join(f"{digest}  {path}\n" for path, digest in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _copy_support_files(source: Path, target: Path) -> None:
    for filename in ("AGENTS.md", "WORKFLOW.md"):
        shutil.copy2(source / filename, target / filename)
    for directory in ("theory", "claims", "reviews", "templates", "outputs", "scripts"):
        src = source / directory
        if src.exists():
            shutil.copytree(src, target / directory)
    book_dir = target / "book"
    book_dir.mkdir(exist_ok=True)
    shutil.copy2(source / "book" / "outline.md", book_dir / "outline.md")
    if (source / "book" / "chapters" / "README.md").exists():
        chapters_doc_dir = book_dir / "chapters"
        chapters_doc_dir.mkdir(exist_ok=True)
        shutil.copy2(source / "book" / "chapters" / "README.md", chapters_doc_dir / "README.md")


def _write_project_files(source: Path, target: Path, *, title: str | None) -> None:
    book_title = title or _book_title(source)
    core = (source / "theory" / "core_definitions.md").read_text(encoding="utf-8")
    outline = (source / "book" / "outline.md").read_text(encoding="utf-8")
    workflow = (source / "WORKFLOW.md").read_text(encoding="utf-8")
    (target / "book.spec.md").write_text(
        f"# 《{book_title}》书稿设定\n\n"
        "## 来源\n"
        f"从 PowerBook 只读导入：`{source.as_posix()}`。\n\n"
        "## 核心命题\n"
        f"{_first_block_after_heading(core, '## 1. 全书核心命题')}\n\n"
        "## 当前阅读队列\n"
        f"{_optional_reading_queue(source)}\n",
        encoding="utf-8",
    )
    (target / "outline.md").write_text(outline, encoding="utf-8")
    (target / "style-guide.md").write_text(
        "# PowerBook 风格与工作流摘要\n\n"
        f"{_first_block_after_heading(workflow, '## 1.1 书稿写法')}\n\n"
        f"{_first_block_after_heading(workflow, '## 1.2 术语翻译规则')}\n",
        encoding="utf-8",
    )
    (target / "rules.yaml").write_text(_rules_yaml(POWERBOOK_RULES), encoding="utf-8")


def _book_title(source: Path) -> str:
    outline = (source / "book" / "outline.md").read_text(encoding="utf-8")
    match = re.search(r'^book_title:\s*"([^"]+)"', outline, re.M)
    return match.group(1) if match else "权力的底层结构"


def _optional_reading_queue(source: Path) -> str:
    path = source / "outputs" / "reading_queue.md"
    if not path.exists():
        return "未找到阅读队列。"
    text = path.read_text(encoding="utf-8")
    return text.strip()


def _first_block_after_heading(text: str, heading: str) -> str:
    start = text.find(heading)
    if start < 0:
        return text.strip().split("\n\n", 1)[0].strip()
    tail = text[start + len(heading) :].strip()
    next_heading = re.search(r"\n##\s+", tail)
    if next_heading:
        tail = tail[: next_heading.start()]
    return tail.strip()


def _rules_yaml(rules: Iterable[Dict[str, Any]]) -> str:
    lines = ["rules:"]
    for rule in rules:
        lines.append(f"  - id: {rule['id']}")
        lines.append(f"    type: {rule['type']}")
        lines.append(f"    text: {rule['text']}")
        lines.append(f"    source_annotations: [{', '.join(rule['source_annotations'])}]")
        lines.append(f"    priority: {rule['priority']}")
        lines.append(f"    apply_to: [{', '.join(rule['apply_to'])}]")
        lines.append(f"    exclude: [{', '.join(rule['exclude'])}]")
        lines.append(f"    status: {rule['status']}")
    return "\n".join(lines) + "\n"


def _convert_chapter(path: Path) -> Tuple[str, Dict[str, Any], List[Dict[str, Any]]]:
    text = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text)
    fields = _frontmatter_fields(frontmatter)
    chapter_number = fields.get("chapter") or _chapter_number_from_name(path.name)
    block_prefix = f"ch{int(chapter_number):02d}" if str(chapter_number).isdigit() else path.stem
    original_status = fields.get("review_status", "draft")
    display_status = STATUS_MAP.get(original_status, "unreviewed")
    edit_status = EDIT_STATUS_MAP.get(display_status, "unreviewed")
    body_without_notes, raw_notes = _extract_author_notes(body)
    heading, remaining = _split_first_h1(body_without_notes)
    blocks = _markdown_blocks(remaining)
    annotations: List[Dict[str, Any]] = []
    lines = [frontmatter.strip(), "", heading.strip()] if frontmatter.strip() else [heading.strip()]
    for index, block_text in enumerate(blocks, start=1):
        block_id = f"{block_prefix}-p{index:03d}"
        lines.extend(["", f"<!-- mw:block id={block_id} hash={current_block_hash(block_text)} -->", block_text])
    for note_index, note in enumerate(raw_notes, start=1):
        target_index = max(1, min(int(note["targetIndex"]), len(blocks))) if blocks else 1
        target_text = blocks[target_index - 1] if blocks else ""
        annotation_id = f"AN-{block_prefix.upper()}-{note_index:03d}"
        annotations.append(
            {
                "id": annotation_id,
                "file": f"chapters/{path.name}",
                "target": {
                    "blockId": f"{block_prefix}-p{target_index:03d}",
                    "selectedText": target_text,
                    "beforeHash": current_block_hash(target_text),
                    "confidence": 0.72,
                    "importedFrom": "PowerBook AUTHOR-NOTE",
                    "originalTarget": note.get("target", ""),
                },
                "body": {
                    "text": note["text"],
                    "type": note.get("type", "other"),
                    "priority": note.get("priority", "medium"),
                },
                "metadata": {
                    "author": "powerbook-import",
                    "status": note.get("status", "open"),
                    "sourceId": note.get("id", annotation_id),
                },
            }
        )
    return "\n".join(line.rstrip() for line in lines).rstrip() + "\n", {
        "chapter": chapter_number,
        "title": fields.get("title", _title_from_h1(heading)),
        "version": fields.get("version", ""),
        "reviewStatus": display_status,
        "reviewRound": fields.get("review_round", ""),
        "bookWorkbenchStatus": edit_status,
        "blockCount": len(blocks),
        "importedAuthorNotes": len(annotations),
    }, annotations


def _split_frontmatter(text: str) -> Tuple[str, str]:
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---", 4)
    if end < 0:
        return "", text
    return text[: end + 4], text[end + 4 :].lstrip("\n")


def _frontmatter_fields(frontmatter: str) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    for line in frontmatter.splitlines():
        if ":" not in line or line.startswith(" ") or line.strip() == "---":
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip().strip('"')
    return fields


def _chapter_number_from_name(name: str) -> str:
    match = re.match(r"ch(\d+)", name)
    return str(int(match.group(1))) if match else "0"


def _split_first_h1(body: str) -> Tuple[str, str]:
    lines = body.strip().splitlines()
    for index, line in enumerate(lines):
        if line.startswith("# "):
            return line, "\n".join(lines[index + 1 :]).strip()
    return f"# {_title_from_h1(body)}", body.strip()


def _title_from_h1(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return "未命名章节"


def _markdown_blocks(text: str) -> List[str]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n+", text.strip()) if block.strip()]
    return [block for block in blocks if not _is_note_block(block)]


def _extract_author_notes(body: str) -> Tuple[str, List[Dict[str, str]]]:
    blocks = [block for block in re.split(r"(\n\s*\n+)", body)]
    output: List[str] = []
    notes: List[Dict[str, str]] = []
    target_index = 0
    for block in blocks:
        if not block.strip():
            output.append(block)
            continue
        if _is_note_block(block):
            notes.append({**_parse_note_block(block), "targetIndex": str(max(target_index, 1))})
            continue
        output.append(block)
        if not block.lstrip().startswith("# "):
            target_index += 1
    return "".join(output), notes


def _is_note_block(block: str) -> bool:
    return any(marker in block for marker in NOTE_MARKERS)


def _parse_note_block(block: str) -> Dict[str, str]:
    cleaned_lines = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            stripped = stripped[1:].lstrip()
        cleaned_lines.append(stripped)
    fields: Dict[str, str] = {}
    body_lines: List[str] = []
    in_body = False
    for line in cleaned_lines:
        if not line or line.startswith("[!"):
            if fields:
                in_body = True
            continue
        if not in_body and ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
        else:
            in_body = True
            body_lines.append(line)
    fields["text"] = "\n".join(body_lines).strip() or block.strip()
    return fields


def _import_discussions(source: Path) -> str:
    reading_queue = source / "outputs" / "reading_queue.md"
    logs = sorted((source / "reviews" / "resolved").glob("*.md")) if (source / "reviews" / "resolved").exists() else []
    items: List[Dict[str, Any]] = []
    if reading_queue.exists():
        items.append(
            {
                "id": "DS-001",
                "type": "discussion",
                "role": "import",
                "text": reading_queue.read_text(encoding="utf-8").strip(),
                "file": "",
                "blockId": "",
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "status": "open",
            }
        )
    for index, path in enumerate(logs[:20], start=2):
        items.append(
            {
                "id": f"DS-{index:03d}",
                "type": "revision_log",
                "role": "import",
                "text": f"已导入修订日志：{path.relative_to(source).as_posix()}\n\n{_first_revision_summary(path)}",
                "file": "",
                "blockId": "",
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "status": "resolved",
            }
        )
    return "\n".join(json.dumps(item, ensure_ascii=False) for item in items)


def _first_revision_summary(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"##\s+1\..*?\n(?P<body>.*?)(?:\n##\s+2\.|\Z)", text, re.S)
    body = match.group("body").strip() if match else text.strip()
    return body[:1200].strip()
