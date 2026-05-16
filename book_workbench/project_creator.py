"""Safe creation of new BookWorkbench manuscript projects."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List


class ProjectCreationError(RuntimeError):
    pass


SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def slugify(value: str, *, fallback: str = "new-book") -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip()).strip("-_").lower()
    if not slug:
        slug = fallback
    if not re.match(r"^[A-Za-z0-9]", slug):
        slug = f"book-{slug}"
    return slug[:64]


def short_hash(text: str, *, length: int = 6) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:length]


def build_project_plan(
    *,
    title: str,
    slug: str | None = None,
    genre: str = "",
    premise: str = "",
    style: str = "",
    chapter_title: str = "第一章",
    opening_text: str = "",
) -> Dict[str, Any]:
    title = title.strip() or "未命名作品"
    project_slug = slugify(slug or title)
    if not SLUG_RE.match(project_slug):
        raise ProjectCreationError(f"Invalid project slug: {project_slug!r}")

    genre = genre.strip() or "未设定"
    premise = premise.strip() or "未设定"
    style = style.strip() or "未设定"
    chapter_title = chapter_title.strip() or "第一章"
    opening_text = opening_text.strip()
    block_hash = f"sha256:{short_hash(opening_text)}"
    files = [
        {
            "path": "book.spec.md",
            "content": (
                f"# 《{title}》Book SPEC\n\n"
                f"## 类型\n{genre}\n\n"
                f"## 核心命题\n{premise}\n\n"
                f"## 风格\n{style}\n"
            ),
        },
        {
            "path": "outline.md",
            "content": f"# 《{title}》大纲\n\n- 第一章：建立人物、压力与选择。\n",
        },
        {
            "path": "style-guide.md",
            "content": f"# Style Guide\n\n- {style}\n- 避免空泛总结心理。\n- 保持章节动作线清楚。\n",
        },
        {
            "path": "rules.yaml",
            "content": (
                "rules:\n"
                "  - id: R-001\n"
                "    type: style\n"
                "    text: 人物心理优先通过动作、停顿、物件和场景压力体现。\n"
                "    source_annotations: []\n"
                "    priority: medium\n"
                "    apply_to: [draft, unreviewed]\n"
                "    exclude: [reviewed, locked]\n"
                "    status: active\n"
            ),
        },
        {
            "path": ".bookai/project.yaml",
            "content": f"title: {title}\nslug: {project_slug}\nversion: 1\n",
        },
        {
            "path": ".bookai/chapter-status.yaml",
            "content": "chapters:\n  chapters/ch01.md:\n    status: draft\n",
        },
        {"path": ".bookai/annotations.jsonl", "content": ""},
        {"path": ".bookai/discussions.jsonl", "content": ""},
        {
            "path": "chapters/ch01.md",
            "content": (
                f"# {chapter_title}\n\n"
                f"<!-- mw:block id=ch01-p001 hash={block_hash} -->\n"
                f"{opening_text}\n"
            ),
        },
    ]
    return {
        "type": "ProjectPlan",
        "slug": project_slug,
        "title": title,
        "files": files,
        "summary": f"Create a new BookWorkbench project for 《{title}》.",
    }


def create_book_project(
    workspace_root: str | Path,
    *,
    title: str,
    slug: str | None = None,
    genre: str = "",
    premise: str = "",
    style: str = "",
    chapter_title: str = "第一章",
    opening_text: str = "",
) -> Dict[str, Any]:
    plan = build_project_plan(
        title=title,
        slug=slug,
        genre=genre,
        premise=premise,
        style=style,
        chapter_title=chapter_title,
        opening_text=opening_text,
    )
    workspace = Path(workspace_root).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    target = (workspace / plan["slug"]).resolve()
    if target.parent != workspace:
        raise ProjectCreationError("Project target escaped the workspace.")
    if target.exists():
        raise ProjectCreationError(f"Project directory already exists: {target}")

    created_files: List[str] = []
    target.mkdir()
    try:
        for file_plan in _safe_files(plan["files"]):
            relative = file_plan["path"]
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(file_plan["content"], encoding="utf-8")
            created_files.append(relative)
    except Exception:
        # Only remove the project we just created, and only while it is still
        # inside the workspace. Avoid shutil.rmtree to keep the cleanup small and
        # predictable for this generated layout.
        for relative in reversed(created_files):
            path = target / relative
            if path.exists():
                path.unlink()
        for directory in sorted((path for path in target.rglob("*") if path.is_dir()), reverse=True):
            directory.rmdir()
        target.rmdir()
        raise

    return {
        "root": target.as_posix(),
        "plan": plan,
        "createdFiles": created_files,
    }


def _safe_files(files: Iterable[Dict[str, Any]]) -> Iterable[Dict[str, str]]:
    for file_plan in files:
        relative = str(file_plan.get("path", ""))
        content = str(file_plan.get("content", ""))
        path = Path(relative)
        if not relative or path.is_absolute() or ".." in path.parts:
            raise ProjectCreationError(f"Unsafe project file path: {relative!r}")
        yield {"path": relative, "content": content}
