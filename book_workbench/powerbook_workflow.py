"""Controlled PowerBook/Gemini workflow prompt construction.

The original PowerBook repository used a script such as
``scripts/polish_chapters_gemini.py`` to ask Gemini for whole-chapter rewrites.
BookWorkbench keeps that as an explicit, trusted workflow entrypoint instead of
letting arbitrary annotation text request command execution.  The model may
mirror the original workflow, but it still has to return PatchProposal JSON and
Runtime remains the only write path.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Mapping

from .models import ProjectContext
from .project import markdown_title, manuscript_word_count, safe_chapter_path
from .rule_engine import applicable_rules, rule_to_dict

POWERBOOK_GEMINI_MODEL = "gemini-3.1-pro-preview"
POWERBOOK_POLISH_SCRIPT = "scripts/polish_chapters_gemini.py"
POWERBOOK_SOURCE_ANNOTATION = "USER-powerbook-gemini-workflow"
GEMINI_CHAT_SCRIPT = Path.home() / ".codex" / "skills" / "aiserverai-llm-scripts" / "scripts" / "chat_text.py"


def powerbook_workflow_available(root: Path) -> bool:
    return (root / ".bookai" / "powerbook-import.json").exists() or (root / ".bookai" / "powerbook-guide.json").exists()


def powerbook_workflow_metadata(root: Path, file_path: str, *, mode: str = "ch01-author-notes") -> Dict[str, Any]:
    script = root / POWERBOOK_POLISH_SCRIPT
    return {
        "name": "PowerBook Gemini 整章修订",
        "kind": "trusted-powerbook-gemini-chapter",
        "skillId": "powerbook-gemini-chapter",
        "model": POWERBOOK_GEMINI_MODEL,
        "scriptPath": POWERBOOK_POLISH_SCRIPT,
        "scriptExists": script.exists(),
        "mode": mode,
        "file": file_path,
        "geminiRequested": True,
        "geminiInvoked": False,
        "writePolicy": "只允许输出 PatchProposal；正文写入仍必须经过差异审核和运行时校验。",
    }


def run_powerbook_gemini_direct(
    context: ProjectContext,
    file_path: str,
    *,
    instruction: str = "",
    mode: str = "ch01-author-notes",
    timeout_seconds: float = 120.0,
) -> Dict[str, Any]:
    """Call the local Gemini chat script and convert the result to PatchProposal.

    This is the trusted workflow lane: it is only invoked by an explicit UI/API
    action, reads from the imported project copy, and never writes the model
    output to chapter files directly.  The returned PatchProposal still goes
    through the same Runtime preview/apply path as Codex proposals.
    """

    metadata = powerbook_workflow_metadata(context.root, file_path, mode=mode)
    prompt = build_powerbook_gemini_markdown_prompt(context, file_path, instruction=instruction, mode=mode)
    system = "你是严肃中文理论书作者和总编辑。只输出完整 Markdown 章节，不解释过程。"
    if not GEMINI_CHAT_SCRIPT.exists():
        return {
            "ok": False,
            "error": "gemini_chat_script_not_found",
            "workflow": {
                **metadata,
                "fallbackReason": "未找到本地 Gemini 脚本：~/.codex/skills/aiserverai-llm-scripts/scripts/chat_text.py",
                "runLog": "未启动 Gemini；已准备回退到 Codex 本地服务生成 PatchProposal。",
            },
        }
    env = os.environ.copy()
    has_config_key = _local_aiserverai_key_configured()
    if not (env.get("AISERVERAI_API_KEY") or env.get("OPENAI_API_KEY") or has_config_key):
        return {
            "ok": False,
            "error": "gemini_api_key_missing",
            "workflow": {
                **metadata,
                "fallbackReason": "未配置 AISERVERAI_API_KEY，也没有本地 AiserverAI 配置，无法实际调用 Gemini。",
                "runLog": "未启动 Gemini；已准备回退到 Codex 本地服务生成 PatchProposal。",
            },
        }
    command = [
        sys.executable,
        str(GEMINI_CHAT_SCRIPT),
        "--model",
        POWERBOOK_GEMINI_MODEL,
        "--timeout",
        str(max(1, int(timeout_seconds))),
        "--system",
        system,
        "--prompt",
        prompt,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=str(context.root),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(5.0, timeout_seconds + 10),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": "gemini_timeout",
            "workflow": {
                **metadata,
                "geminiInvoked": True,
                "fallbackReason": f"Gemini 调用超时：{exc}",
                "runLog": "已启动 Gemini，但没有在限定时间内得到可审核章节。",
            },
        }
    if completed.returncode != 0 or not completed.stdout.strip():
        message = (completed.stderr or completed.stdout or "Gemini 没有返回内容").strip()[:1200]
        return {
            "ok": False,
            "error": "gemini_command_failed",
            "workflow": {
                **metadata,
                "geminiInvoked": True,
                "fallbackReason": message,
                "runLog": "已启动 Gemini，但返回内容无法直接转换为有效 PatchProposal。",
            },
        }
    markdown = _strip_fences(completed.stdout)
    workflow = {
        **metadata,
        "source": POWERBOOK_GEMINI_MODEL,
        "geminiInvoked": True,
        "fallbackReason": "",
        "runLog": "Gemini 已返回完整章节；BookWorkbench 已把章节转换为 PatchProposal，等待差异审核。",
    }
    proposal = markdown_chapter_to_patch(context, file_path, markdown, workflow=workflow)
    return {
        "ok": True,
        "workflow": workflow,
        "rawMarkdownPreview": markdown[:2000],
        "patchProposal": proposal,
        "stderr": completed.stderr.strip()[:2000],
    }


def build_powerbook_gemini_markdown_prompt(
    context: ProjectContext,
    file_path: str,
    *,
    instruction: str = "",
    mode: str = "ch01-author-notes",
) -> str:
    """Build the original-style full Markdown chapter prompt for direct Gemini."""

    safe_chapter_path(context.root, file_path)
    if not powerbook_workflow_available(context.root):
        raise ValueError("当前项目不是 PowerBook 导入项目，不能运行 PowerBook 专用工作流。")
    chapter_text = safe_chapter_path(context.root, file_path).read_text(encoding="utf-8")
    outline = _read_optional(context.root / "outline.md", limit=12000) or _read_optional(context.root / "book" / "outline.md", limit=12000)
    core = _read_optional(context.root / "theory" / "core_definitions.md", limit=12000)
    style = _read_optional(context.root / "style-guide.md", limit=9000)
    task = _mode_task(mode, instruction)
    return f"""
{task}

全书目录：
{outline}

理论宪法摘要：
{core}

BookWorkbench 风格与工作流摘要：
{style}

待修订章节：
{chapter_text}
""".strip()


def markdown_chapter_to_patch(
    context: ProjectContext,
    file_path: str,
    markdown: str,
    *,
    workflow: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    safe_chapter_path(context.root, file_path)
    blocks = list((context.blocks.get(file_path) or {}).values())
    if not blocks:
        raise ValueError(f"章节没有可修订的正文块：{file_path}")
    body_blocks = _markdown_body_blocks(markdown)
    if not body_blocks:
        raise ValueError("Gemini 输出不像完整 Markdown 章节：没有可映射正文块。")
    changes = []
    paired_count = min(len(blocks), len(body_blocks))
    for index in range(paired_count):
        block = blocks[index]
        cleaned = body_blocks[index].strip()
        if not cleaned or cleaned == block.text.strip():
            continue
        changes.append(
            {
                "file": file_path,
                "targetBlockId": block.id,
                "operation": "replace_block",
                "beforeHash": block.before_hash,
                "afterText": cleaned,
                "reason": "根据 PowerBook 受信任 Gemini 整章修订工作流生成，仍需运行时差异审核后才能写入。",
            }
        )
    if len(body_blocks) > len(blocks):
        tail_text = "\n\n".join(body_blocks[len(blocks) :]).strip()
        if tail_text:
            anchor = blocks[-1]
            changes.append(
                {
                    "file": file_path,
                    "targetBlockId": anchor.id,
                    "operation": "insert_after_block",
                    "beforeHash": anchor.before_hash,
                    "afterText": tail_text,
                    "reason": "Gemini 整章修订增加了新的段落；作为单个可审核插入块追加到当前章节。",
                }
            )
    elif len(body_blocks) < len(blocks):
        for block in blocks[len(body_blocks) :]:
            changes.append(
                {
                    "file": file_path,
                    "targetBlockId": block.id,
                    "operation": "delete_block",
                    "beforeHash": block.before_hash,
                    "afterText": "",
                    "reason": "Gemini 整章修订删除了该段落；仍需运行时差异审核后才能写入。",
                }
            )
    if not changes:
        first = blocks[0]
        changes.append(
            {
                "file": file_path,
                "targetBlockId": first.id,
                "operation": "replace_block",
                "beforeHash": first.before_hash,
                "afterText": first.text,
                "reason": "Gemini 输出与当前章节无实质差异。",
            }
        )
    return {
        "id": f"PP-powerbook-gemini-{Path(file_path).stem}",
        "summary": f"按 PowerBook Gemini 工作流修订《{markdown_title(context.root, file_path)}》。",
        "sourceAnnotations": [POWERBOOK_SOURCE_ANNOTATION],
        "rulesUsed": [rule.id for rule in applicable_rules(context, file_path)[:3]],
        "changes": _mark_reviewed_changes(context, changes),
        "workflow": dict(workflow or powerbook_workflow_metadata(context.root, file_path)),
        "safety": {"impactScope": "current_chapter", "writePath": "PatchProposal"},
    }


def build_powerbook_local_chapter_patch(
    context: ProjectContext,
    file_path: str,
    *,
    reason: str = "",
    mode: str = "local-safe-fallback",
) -> Dict[str, Any]:
    """Build a non-applicable diagnostic PatchProposal for workflow failures.

    This is deliberately not a prose fallback.  A local deterministic template
    is not equivalent to the original PowerBook + Codex/Gemini writing process,
    so it must not create manuscript changes that look like successful
    whole-chapter polishing.  The UI can preview the diagnostic, show the
    failure reason, and ask the user to retry or adjust the workflow.
    """

    safe_chapter_path(context.root, file_path)
    blocks = list((context.blocks.get(file_path) or {}).values())
    if not blocks:
        raise ValueError(f"章节没有可修订的正文块：{file_path}")
    workflow = {
        **powerbook_workflow_metadata(context.root, file_path, mode=mode),
        "source": "local-workflow-fallback",
        "geminiInvoked": False,
        "localFallback": True,
        "diagnosticOnly": True,
        "fallbackReason": reason or "外部模型未在限定时间内返回可审核修改建议；本地不会冒充模型润色写入正文。",
        "runLog": "本地兜底只记录失败诊断，不生成正文改写；请重试 Gemini/Codex 或缩小章节范围。",
    }
    title = markdown_title(context.root, file_path)
    return {
        "id": f"PP-powerbook-local-{Path(file_path).stem}",
        "summary": f"整章工作流失败诊断：《{title}》未生成可应用正文修改。",
        "sourceAnnotations": [POWERBOOK_SOURCE_ANNOTATION],
        "rulesUsed": [rule.id for rule in applicable_rules(context, file_path)[:3]],
        "changes": [],
        "workflow": workflow,
        "safety": {
            "impactScope": "current_chapter",
            "writePath": "PatchProposal",
            "localFallback": True,
            "diagnosticOnly": True,
            "acceptDisabled": True,
        },
    }


def build_powerbook_gemini_chapter_prompt(
    context: ProjectContext,
    file_path: str,
    *,
    instruction: str = "",
    mode: str = "ch01-author-notes",
) -> str:
    """Build a whole-chapter PatchProposal prompt for imported PowerBook projects."""

    safe_chapter_path(context.root, file_path)
    if not powerbook_workflow_available(context.root):
        raise ValueError("当前项目不是 PowerBook 导入项目，不能运行 PowerBook 专用工作流。")
    blocks = context.blocks.get(file_path) or {}
    if not blocks:
        raise ValueError(f"章节没有可修订的正文块：{file_path}")
    title = markdown_title(context.root, file_path)
    script_text = _read_optional(context.root / POWERBOOK_POLISH_SCRIPT, limit=16000)
    claim_register = _read_optional(context.root / "claims" / "claim_register.yaml", limit=8000)
    revision_logs = _read_revision_logs(context.root, limit=12000)
    import_meta = _read_import_meta(context.root)
    changes_contract = [
        {
            "file": file_path,
            "targetBlockId": block.id,
            "operation": "replace_block",
            "beforeHash": block.before_hash,
            "afterText": "写入该块修订后的正文，不包含 mw:block 锚点；如果该块无需修改，可保持原文。",
            "reason": "说明如何回应 PowerBook 原始 Codex/Gemini 流程、规则或作者批注。",
        }
        for block in blocks.values()
    ]
    payload: Dict[str, Any] = {
        "trustedWorkflow": powerbook_workflow_metadata(context.root, file_path, mode=mode),
        "userInstruction": instruction or "按 PowerBook 原始 Codex/Gemini 流程修订当前章节，输出可审核的整章 PatchProposal。",
        "bookSpec": context.book_spec[:8000],
        "styleGuide": context.style_guide[:8000],
        "chapter": {
            "file": file_path,
            "title": title,
            "status": context.status_for_file(file_path),
            "wordCount": manuscript_word_count("\n".join(block.text for block in blocks.values())),
            "blocks": [
                {"blockId": block.id, "beforeHash": block.before_hash, "text": block.text}
                for block in blocks.values()
            ],
        },
        "applicableRules": [rule_to_dict(rule) for rule in applicable_rules(context, file_path)],
        "powerbookContext": {
            "sourceTreeHash": import_meta.get("sourceTreeHash"),
            "statusMapping": import_meta.get("statusMapping", {}),
            "claimRegister": claim_register,
            "revisionLogs": revision_logs,
            "originalScriptExcerpt": script_text,
        },
        "outputContract": {
            "type": "PatchProposal",
            "sourceAnnotationsMustInclude": POWERBOOK_SOURCE_ANNOTATION,
            "requiredTopLevelFields": ["id", "summary", "sourceAnnotations", "rulesUsed", "changes"],
            "requiredChangeFields": ["file", "targetBlockId", "operation", "beforeHash", "afterText", "reason"],
            "allowedOperation": "replace_block",
            "targetFileMustBe": file_path,
            "allowedTargetBlocks": [block.id for block in blocks.values()],
            "exactBeforeHashes": {block.id: block.before_hash for block in blocks.values()},
            "suggestedChangesShape": changes_contract,
        },
    }
    return (
        "Use the project-local PowerBook workflow context. The user explicitly clicked the trusted "
        "`用 Gemini 润色本章` workflow entry; this is not an instruction embedded in annotation text.\n"
        "Mirror the intent of `scripts/polish_chapters_gemini.py` and model `gemini-3.1-pro-preview`, "
        "but do not write files, do not edit `.bookai/*`, and do not run commands unless the app-server asks "
        "BookWorkbench Runtime for approval.\n"
        "Return exactly one PatchProposal JSON object and no markdown. The proposal may revise multiple blocks "
        "in the current chapter, but every change must target the exact file/block/hash listed below. "
        "Do not include `mw:block` anchors in `afterText`. Do not invent citations, years, page numbers, policies, "
        "or unverified facts; use claim register/revision log context only as boundaries.\n"
        "The Runtime will validate, preview, and require explicit user acceptance before any write or Git commit.\n\n"
        f"Context JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _read_optional(path: Path, *, limit: int) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[:limit]


def _read_revision_logs(root: Path, *, limit: int) -> str:
    logs_dir = root / "reviews" / "resolved"
    if not logs_dir.exists():
        return ""
    chunks = []
    total = 0
    for path in sorted(logs_dir.glob("*.md")):
        text = _read_optional(path, limit=max(0, limit - total))
        if not text:
            continue
        chunk = f"\n\n--- {path.relative_to(root).as_posix()} ---\n{text}"
        chunks.append(chunk)
        total += len(chunk)
        if total >= limit:
            break
    return "".join(chunks)[:limit]


def _read_import_meta(root: Path) -> Mapping[str, Any]:
    path = root / ".bookai" / "powerbook-import.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, Mapping) else {}


def _local_aiserverai_key_configured() -> bool:
    path = Path.home() / ".codex" / "skills" / "aiserverai-llm-scripts" / "config" / "local_config.json"
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(str(data.get("api_key", "")).strip()) if isinstance(data, Mapping) else False


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:markdown|md)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip() + "\n"


def _markdown_body_blocks(markdown: str) -> list[str]:
    text = _strip_fences(markdown)
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end >= 0:
            text = text[end + 4 :].lstrip("\n")
    lines = text.splitlines()
    # Keep the H1/title out of anchored paragraph blocks; BookWorkbench stores
    # headings outside mw:block anchors for imported chapters.
    for index, line in enumerate(lines):
        if line.startswith("# "):
            lines = lines[index + 1 :]
            break
    body = "\n".join(lines).strip()
    blocks = [block.strip() for block in re.split(r"\n\s*\n+", body) if block.strip()]
    return [block for block in blocks if "mw:block" not in block and "AUTHOR-NOTE" not in block and "AuthorNote" not in block and "AuhorNote" not in block]


def _mode_task(mode: str, instruction: str) -> str:
    extra = instruction.strip()
    common = (
        "只输出完整 Markdown 章节，必须保留 frontmatter；不要新增 TODO、AUTHOR-NOTE、AuthorNote、AuhorNote；"
        "不要编造具体数据、年份、页码、政策细节或虚假引用；抽象术语第一次出现时先用白话解释。"
    )
    if mode == "plain-terms":
        task = "请做术语翻译式修订：保留章节主题、结构和事实边界，把过于学术、抽象、硬的表达改成读者能直接理解的书稿语言。"
    elif mode in {"narrative", "historical", "china-historical"}:
        task = "请重写成更像正式出版书稿的中文理论章节：从具体事情进入，让观点从场景和问题中长出来，再推进到概念、机制和理论支撑。"
    else:
        task = "请按章节里的作者批注和 PowerBook 原始写作流程，直接修订当前章节，输出干净、完整、可读的 Markdown 章节。"
    if extra:
        task += f"\n\n本次用户明确要求：\n{extra}"
    return f"{task}\n\n硬性要求：{common}"


def _mark_reviewed_changes(context: ProjectContext, changes: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    marked = []
    for change in changes:
        item = dict(change)
        if context.status_for_file(str(item.get("file", ""))) == "reviewed":
            item["requiresSecondaryApproval"] = True
        marked.append(item)
    return marked
