"""PowerBook workflow memory extraction.

The original PowerBook quality came from a long autonomous loop: project rules,
AUTHOR-NOTE rounds, resolved revision logs, backups, claim register updates, and
occasionally Gemini polish.  BookWorkbench stores that loop as first-class
project memory so autonomous runs can read more than the current chapter text
without mutating the read-only source project.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

MAX_MEMORY_TEXT_CHARS = 120_000


WORKFLOW_FILES = [
    "AGENTS.md",
    "WORKFLOW.md",
    "theory/core_definitions.md",
    "theory/theory_change_requests.md",
    "book/outline.md",
    "claims/claim_register.yaml",
    "outputs/reading_queue.md",
    "outputs/v0_1_author_reading_guide.md",
]


REVISION_ACTION_PATTERNS = (
    r"^\s*\d+[\.、]\s*(.+)$",
    r"^\s*[-*]\s*(.+)$",
    r"^\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|",
)


def build_powerbook_memory(root: str | Path) -> Dict[str, Any]:
    """Return a compact, deterministic memory pack for PowerBook-style runs."""

    project_root = Path(root).resolve()
    files = _memory_files(project_root)
    revision_logs = _revision_logs(project_root)
    backups = _backup_index(project_root)
    artifacts = _artifact_inventory(project_root, files, revision_logs, backups)
    excerpts = _bounded_excerpts(project_root, files, revision_logs, backups)
    decisions = _extract_decisions(project_root, revision_logs)
    initial_prompt = _initial_prompt(project_root)
    return {
        "kind": "powerbook-autonomous-memory",
        "version": 1,
        "summary": _memory_summary(project_root, revision_logs, backups, decisions),
        "initialPrompt": initial_prompt,
        "mustRead": [item["path"] for item in files[:8]],
        "revisionLogCount": len(revision_logs),
        "backupSnapshotCount": len(backups),
        "artifacts": artifacts,
        "decisions": decisions[:40],
        "excerpts": excerpts,
    }


def write_powerbook_memory(root: str | Path) -> Dict[str, Any]:
    """Write `.bookai/powerbook-memory.json` and return the memory object."""

    project_root = Path(root).resolve()
    memory = build_powerbook_memory(project_root)
    target = project_root / ".bookai" / "powerbook-memory.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(memory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return memory


def _memory_files(root: Path) -> list[Dict[str, Any]]:
    items: list[Dict[str, Any]] = []
    for relative in WORKFLOW_FILES:
        path = root / relative
        if path.exists() and path.is_file():
            items.append(_file_item(root, path, role="workflow"))
    for directory in (root / "reviews" / "inbox", root / "templates"):
        if directory.exists():
            for path in sorted(directory.glob("*.md")):
                items.append(_file_item(root, path, role="workflow"))
    return items


def _revision_logs(root: Path) -> list[Dict[str, Any]]:
    directory = root / "reviews" / "resolved"
    if not directory.exists():
        return []
    return [_file_item(root, path, role="revision_log") for path in sorted(directory.glob("*.md"))]


def _backup_index(root: Path) -> list[Dict[str, Any]]:
    directory = root / "outputs" / "backups"
    if not directory.exists():
        return []
    items: list[Dict[str, Any]] = []
    for path in sorted(directory.rglob("*.md")):
        if len(items) >= 80:
            break
        items.append(_file_item(root, path, role="backup"))
    return items


def _artifact_inventory(root: Path, *groups: Iterable[Mapping[str, Any]]) -> list[Dict[str, Any]]:
    artifacts: list[Dict[str, Any]] = []
    for group in groups:
        for item in group:
            artifacts.append(
                {
                    "path": str(item.get("path", "")),
                    "role": str(item.get("role", "")),
                    "chars": int(item.get("chars", 0) or 0),
                    "label": _label_for_path(str(item.get("path", ""))),
                }
            )
    return artifacts


def _bounded_excerpts(root: Path, files: list[Mapping[str, Any]], revision_logs: list[Mapping[str, Any]], backups: list[Mapping[str, Any]]) -> list[Dict[str, str]]:
    selected: list[Mapping[str, Any]] = []
    selected.extend(files[:12])
    selected.extend(revision_logs[-12:])
    selected.extend(_important_backups(backups))
    excerpts: list[Dict[str, str]] = []
    used = 0
    seen: set[str] = set()
    for item in selected:
        relative = str(item.get("path", ""))
        if not relative or relative in seen:
            continue
        seen.add(relative)
        path = root / relative
        if not path.exists() or not path.is_file():
            continue
        remaining = MAX_MEMORY_TEXT_CHARS - used
        if remaining <= 0:
            break
        text = path.read_text(encoding="utf-8", errors="replace")
        excerpt = text[: min(12000, remaining)]
        if not excerpt:
            continue
        excerpts.append({"path": relative, "text": excerpt})
        used += len(excerpt)
    return excerpts


def _important_backups(backups: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    preferred = []
    markers = (
        "ch01_before_authornote",
        "ch01_before_historical_opening",
        "ch01_before_v07_authornotes",
        "chapters_before_gemini_polish",
        "chapters_before_plain_terms",
    )
    for marker in markers:
        for item in backups:
            path = str(item.get("path", ""))
            if marker in path and path.endswith("ch01_power.md"):
                preferred.append(item)
                break
    return preferred


def _extract_decisions(root: Path, revision_logs: list[Mapping[str, Any]]) -> list[Dict[str, str]]:
    decisions: list[Dict[str, str]] = []
    for item in revision_logs:
        relative = str(item.get("path", ""))
        path = root / relative
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("|---"):
                continue
            matched = _decision_from_line(stripped)
            if matched:
                decisions.append({"source": relative, "text": matched})
    return decisions


def _decision_from_line(line: str) -> str:
    for pattern in REVISION_ACTION_PATTERNS:
        match = re.match(pattern, line)
        if not match:
            continue
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) >= 3 and cells[0].lower() not in {"id", "---"}:
                return f"{cells[0]}：{cells[-1]}"[:500]
        value = match.group(1).strip()
        if _looks_like_decision(value):
            return value[:500]
    return ""


def _looks_like_decision(text: str) -> bool:
    markers = ("改", "修订", "删除", "增加", "补", "使用", "调用", "跳过", "保留", "登记", "查证", "标题", "开头", "Gemini", "Codex")
    return any(marker in text for marker in markers) and len(text) >= 8


def _initial_prompt(root: Path) -> Dict[str, Any]:
    guide_path = root / ".bookai" / "powerbook-guide.json"
    if not guide_path.exists():
        return {}
    try:
        data = json.loads(guide_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        "digest": data.get("initialPromptDigest", ""),
        "excerpt": data.get("initialPromptExcerpt", ""),
        "inputCount": data.get("initialInputCount", 0),
    }


def _memory_summary(root: Path, revision_logs: list[Mapping[str, Any]], backups: list[Mapping[str, Any]], decisions: list[Mapping[str, str]]) -> str:
    return (
        "原始质量来自完整章节优先、作者批注保护、修订日志复盘、证据登记、历史备份对照和 Gemini/Codex 多轮润色；"
        f"当前项目可读取 {len(revision_logs)} 个修订日志、{len(backups)} 个备份章节片段、{len(decisions)} 条可抽取处理决定。"
    )


def _file_item(root: Path, path: Path, *, role: str) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "path": path.relative_to(root).as_posix(),
        "role": role,
        "chars": len(text),
        "headings": [line.strip() for line in text.splitlines() if line.startswith("#")][:12],
    }


def _label_for_path(path: str) -> str:
    if path == "AGENTS.md":
        return "项目约定"
    if path == "WORKFLOW.md":
        return "写作流程"
    if "revision_log" in path or "reviews/resolved" in path:
        return "修订日志"
    if "backups" in path:
        return "历史备份"
    if "claim" in path:
        return "证据登记"
    if "reading_queue" in path:
        return "阅读队列"
    return Path(path).name
