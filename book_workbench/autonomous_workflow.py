"""Autonomous scratch workflow for BookWorkbench.

This module is the seam for the product pivot from a rigid Runtime-only helper
into an autonomous Codex writing workbench.  The important boundary is still
unchanged: Codex/Gemini may explore and edit a scratch copy, but the official
manuscript project is changed only by a Runtime-validated PatchProposal that the
user previews and accepts.
"""

from __future__ import annotations

import difflib
import json
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping

from .codex_workflow import patch_has_changes, summarize_codex_result, validation_is_valid
from .models import ProjectContext
from .patch_engine import preview_diff
from .powerbook_workflow import POWERBOOK_SOURCE_ANNOTATION, markdown_chapter_to_patch, powerbook_workflow_available
from .powerbook_memory import build_powerbook_memory
from .project import load_project, manuscript_word_count, markdown_title, safe_chapter_path
from .rule_engine import applicable_rules, rule_to_dict

AUTONOMOUS_SOURCE_ANNOTATION = "USER-autonomous-workflow"
AUTONOMOUS_WORKFLOW_KIND = "autonomous-codex-scratch"
RUNS_DIR = ".bookai/runs"

ApprovalHandler = Callable[[Dict[str, Any]], Dict[str, Any]]


def run_autonomous_workflow(
    *,
    runtime: Any,
    codex_client: Any,
    file_path: str,
    goal: str = "",
    mode: str = "chapter-revision",
    timeout_seconds: float = 180.0,
) -> Dict[str, Any]:
    """Run an autonomous writing pass in scratch and return a safe proposal.

    ``runtime`` is intentionally duck-typed to avoid a circular import.  It must
    provide ``refreshed_context`` and ``validate_patch``.  No official chapter
    file is written here; all official writes still go through Runtime preview /
    accept endpoints after this function returns.
    """

    context: ProjectContext = runtime.refreshed_context()
    safe_chapter_path(context.root, file_path)
    run_id = _new_run_id()
    run_dir = context.root / RUNS_DIR / run_id
    artifacts_dir = run_dir / "artifacts"
    scratch_root = run_dir / "scratch"
    artifacts_dir.mkdir(parents=True, exist_ok=False)
    _ensure_runs_git_ignored(context.root)
    _copy_project_to_scratch(context.root, scratch_root)

    workflow = _workflow_metadata(context, file_path, run_id=run_id, mode=mode)
    prompt = build_autonomous_codex_prompt(context, file_path, goal=goal, mode=mode, run_id=run_id)
    _write_artifact(artifacts_dir, "run-plan.md", _run_plan_markdown(context, file_path, goal=goal, mode=mode, run_id=run_id))
    _write_artifact(artifacts_dir, "prompt.md", prompt)
    _write_jsonl(run_dir / "events.jsonl", {"type": "autonomous.run.started", "runId": run_id, "file": file_path, "mode": mode})

    before_text = safe_chapter_path(context.root, file_path).read_text(encoding="utf-8")
    codex_result: Mapping[str, Any]
    started = time.monotonic()
    try:
        if not hasattr(codex_client, "run_autonomous_turn"):
            raise RuntimeError("codex_autonomous_turn_unavailable")
        codex_result = codex_client.run_autonomous_turn(
            prompt=prompt,
            cwd=scratch_root,
            approval_handler=_scratch_approval_handler(scratch_root),
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:  # defensive local integration boundary
        codex_result = {"ok": False, "error": str(exc), "durationMs": int((time.monotonic() - started) * 1000)}
    codex_summary = summarize_codex_result(codex_result)
    # ``summarize_codex_result`` is PatchProposal-oriented; preserve the raw
    # autonomous turn basics explicitly for artifact/debug UI.
    codex_summary.update(
        {
            "ok": bool(codex_result.get("ok")),
            "error": codex_result.get("error"),
            "threadId": codex_result.get("threadId"),
            "turnId": codex_result.get("turnId"),
            "durationMs": codex_result.get("durationMs"),
            "finalTextPreview": str(codex_result.get("finalText") or "")[:1200],
        }
    )
    _write_jsonl(artifacts_dir / "model-calls.jsonl", {"type": "codex.autonomous.turn", "summary": codex_summary})
    _copy_scratch_autonomous_artifacts(scratch_root, artifacts_dir)

    scratch_chapter = scratch_root / file_path
    if not scratch_chapter.exists():
        reason = f"scratch_chapter_missing:{file_path}"
        return _diagnostic_response(
            runtime=runtime,
            context=context,
            file_path=file_path,
            run_id=run_id,
            run_dir=run_dir,
            artifacts_dir=artifacts_dir,
            workflow=workflow,
            reason=reason,
            codex_summary=codex_summary,
        )

    after_text = scratch_chapter.read_text(encoding="utf-8")
    if _normalized_chapter_text(before_text) == _normalized_chapter_text(after_text):
        reason = str(codex_result.get("error") or "autonomous_run_produced_no_chapter_diff")
        return _diagnostic_response(
            runtime=runtime,
            context=context,
            file_path=file_path,
            run_id=run_id,
            run_dir=run_dir,
            artifacts_dir=artifacts_dir,
            workflow=workflow,
            reason=reason,
            codex_summary=codex_summary,
        )

    scratch_context = load_project(scratch_root)
    patch = _scratch_chapter_to_patch(context, scratch_context, file_path, workflow=workflow)
    quality = _quality_report(context, scratch_context, file_path, patch)
    validation = runtime.validate_patch(patch)
    patch["workflow"]["patchValidation"] = validation
    patch["workflow"]["qualityReport"] = quality
    _write_json(artifacts_dir / "quality-report.json", quality)

    if not validation_is_valid(validation) or not patch_has_changes(patch) or not _patch_is_meaningful(context, patch):
        reason = _validation_issue_summary(validation) or "autonomous_patch_failed_quality_gate"
        _write_json(artifacts_dir / "rejected-patch-proposal.json", patch)
        _write_artifact(artifacts_dir, "scratch-diff.patch", _unified_text_diff(before_text, after_text, file_path=file_path))
        return _diagnostic_response(
            runtime=runtime,
            context=context,
            file_path=file_path,
            run_id=run_id,
            run_dir=run_dir,
            artifacts_dir=artifacts_dir,
            workflow={**workflow, "qualityReport": quality, "rejectedPatchValidation": validation},
            reason=reason,
            codex_summary=codex_summary,
        )

    diff_text = preview_diff(context, patch)
    artifact_snapshot = _artifact_list(context.root, run_dir)
    patch["workflow"]["artifacts"] = artifact_snapshot
    _write_json(artifacts_dir / "patch-proposal.json", patch)
    _write_artifact(artifacts_dir, "final-diff.patch", diff_text)
    artifacts = _artifact_list(context.root, run_dir)
    patch["workflow"]["artifacts"] = artifacts
    _write_json(artifacts_dir / "patch-proposal.json", patch)
    _write_jsonl(run_dir / "events.jsonl", {"type": "autonomous.patch.ready", "runId": run_id, "patchId": patch["id"]})
    return {
        "runId": run_id,
        "skill": "autonomous-writing-workflow",
        "source": "autonomous-codex-scratch",
        "workflow": patch["workflow"],
        "events": [
            {"type": "autonomous.run.started", "runId": run_id, "file": file_path},
            {"type": "autonomous.codex.completed", "summary": codex_summary},
            {"type": "autonomous.patch.ready", "patchId": patch["id"], "quality": quality},
        ],
        "output": patch,
        "codex": codex_summary,
        "artifacts": _artifact_list(context.root, run_dir),
    }


def build_autonomous_codex_prompt(
    context: ProjectContext,
    file_path: str,
    *,
    goal: str = "",
    mode: str = "chapter-revision",
    run_id: str = "RUN-preview",
) -> str:
    safe_chapter_path(context.root, file_path)
    blocks = list((context.blocks.get(file_path) or {}).values())
    import_meta = _read_json(context.root / ".bookai" / "powerbook-import.json")
    guide_meta = _read_json(context.root / ".bookai" / "powerbook-guide.json")
    workflow_memory = build_powerbook_memory(context.root) if powerbook_workflow_available(context.root) else {}
    payload: Dict[str, Any] = {
        "runId": run_id,
        "workflow": {
            "kind": AUTONOMOUS_WORKFLOW_KIND,
            "mode": mode,
            "targetFile": file_path,
            "writeBoundary": "You are in an isolated copy. Edit isolated-copy files only; official manuscript writes are forbidden.",
            "handoff": "BookWorkbench will diff isolated-copy output and convert it to a Runtime-reviewed change proposal.",
        },
        "userGoal": goal or "按项目已有 AGENTS / WORKFLOW / rules / 作者批注，自主安排本章修订，产出可审核的完整章节改写。",
        "bookSpec": context.book_spec[:10000],
        "styleGuide": context.style_guide[:10000],
        "targetChapter": {
            "file": file_path,
            "title": markdown_title(context.root, file_path),
            "status": context.status_for_file(file_path),
            "wordCount": manuscript_word_count("\n".join(block.text for block in blocks)),
            "blocks": [{"blockId": block.id, "beforeHash": block.before_hash, "text": block.text} for block in blocks],
        },
        "applicableRules": [rule_to_dict(rule) for rule in applicable_rules(context, file_path)],
        "powerbookContext": {
            "enabled": powerbook_workflow_available(context.root),
            "import": import_meta,
            "guide": guide_meta,
            "workflowMemory": workflow_memory,
            "agentsExcerpt": _read_optional(context.root / "AGENTS.md", 9000),
            "workflowExcerpt": _read_optional(context.root / "WORKFLOW.md", 12000),
            "claimRegisterExcerpt": _read_optional(context.root / "claims" / "claim_register.yaml", 12000),
            "revisionLogsExcerpt": _read_revision_logs(context.root, 14000),
        },
        "requiredScratchArtifacts": [
            ".bookai/autonomous/run-plan.md",
            ".bookai/autonomous/rules-delta.yaml",
            ".bookai/autonomous/revision-log.md",
            ".bookai/autonomous/quality-report.json",
        ],
    }
    return (
        "你正在 BookWorkbench 的隔离副本里运行自主写作工作流。\n"
        "目标是尽量复现原 PowerBook 式自主流程：先读项目约定、工作流、规则、作者批注、修订日志、历史备份和证据登记，"
        "再自己安排步骤；必要时可在隔离副本内写 run-plan、rules-delta、revision-log、quality-report 等调试资料。\n"
        "硬边界：只允许修改当前隔离副本工作目录；不要访问或修改原始 PowerBook 源目录；不要把正式项目当成写入目标。"
        "最终请把目标章节文件本身修订到你认为可交付的状态。BookWorkbench 会在外层读取隔离副本章节，转换成安全修改建议，"
        "再由运行时校验、差异审核和版本提交。\n"
        "质量要求：如果是完整章节修订，不要只改一句模板话；要保持章节密度、结构、白话解释、理论推进和事实边界。"
        "不要编造具体引用、年份、页码、政策细节或虚假数据；不确定内容写入 claim/quality artifact，而不是冒充正文事实。\n\n"
        f"Context JSON:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def load_run_status(root: Path, run_id: str) -> Dict[str, Any]:
    run_dir = _safe_run_dir(root, run_id)
    events_path = run_dir / "events.jsonl"
    events = []
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                events.append({"type": "invalid_event", "raw": line})
    patch_path = run_dir / "artifacts" / "patch-proposal.json"
    patch = _read_json(patch_path) if patch_path.exists() else None
    return {
        "runId": run_id,
        "exists": True,
        "events": events,
        "artifacts": _artifact_list(root, run_dir),
        "patchId": patch.get("id") if isinstance(patch, Mapping) else None,
        "hasPatch": isinstance(patch, Mapping),
    }


def load_run_artifacts(root: Path, run_id: str, *, include_text: bool = False) -> Dict[str, Any]:
    run_dir = _safe_run_dir(root, run_id)
    artifacts = _artifact_list(root, run_dir)
    if include_text:
        for artifact in artifacts:
            path = root / artifact["path"]
            if path.is_file() and path.stat().st_size <= 200_000:
                artifact["text"] = path.read_text(encoding="utf-8", errors="replace")
    return {"runId": run_id, "artifacts": artifacts}


def load_run_patch(root: Path, run_id: str) -> Dict[str, Any]:
    run_dir = _safe_run_dir(root, run_id)
    path = run_dir / "artifacts" / "patch-proposal.json"
    if not path.exists():
        path = run_dir / "artifacts" / "diagnostic-patch-proposal.json"
    if not path.exists():
        raise ValueError(f"自主工作流没有可提交 PatchProposal：{run_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"自主工作流 PatchProposal 不是对象：{run_id}")
    return data


def _workflow_metadata(context: ProjectContext, file_path: str, *, run_id: str, mode: str) -> Dict[str, Any]:
    return {
        "name": "自主写作工作流",
        "kind": AUTONOMOUS_WORKFLOW_KIND,
        "skillId": "autonomous-writing-workflow",
        "source": "autonomous-codex-scratch",
        "model": "codex-app-server",
        "mode": mode,
        "file": file_path,
        "runId": run_id,
        "scratchPolicy": "智能服务只能写隔离副本；正式书稿只通过安全修改建议写入。",
        "writePolicy": "差异审核前不写 chapters/*.md；用户接受后才由 Runtime 写入并创建 Git 提交。",
        "runLog": "智能服务在隔离副本中自主修订；BookWorkbench 已把隔离副本差异转换为可审核修改建议。",
        "artifacts": [],
        "powerbookContext": powerbook_workflow_available(context.root),
    }


def _scratch_chapter_to_patch(
    official: ProjectContext,
    scratch: ProjectContext,
    file_path: str,
    *,
    workflow: Mapping[str, Any],
) -> Dict[str, Any]:
    official_blocks = list((official.blocks.get(file_path) or {}).values())
    scratch_blocks_by_id = scratch.blocks.get(file_path) or {}
    sources = _autonomous_sources(official)
    if not scratch_blocks_by_id:
        # A genuinely autonomous Codex pass may rewrite the chapter as clean
        # Markdown and drop ``mw:block`` anchors.  That should not force a
        # fake failure: treat the scratch chapter as a whole-chapter manuscript
        # draft, map it back to the current official blocks, then let Runtime
        # validation and quality gates decide whether it is safe enough to
        # preview.  The official project is still untouched at this point.
        markdown = safe_chapter_path(scratch.root, file_path).read_text(encoding="utf-8")
        patch = markdown_chapter_to_patch(official, file_path, markdown, workflow=workflow)
        patch["id"] = f"PP-autonomous-{Path(file_path).stem}-{workflow.get('runId', 'run')}"
        patch["summary"] = f"自主写作工作流修订《{markdown_title(official.root, file_path)}》。"
        patch["sourceAnnotations"] = sources
        patch["workflow"] = dict(workflow)
        patch["safety"] = {"impactScope": "current_chapter", "writePath": "PatchProposal", "scratchOnly": True}
        return patch

    changes = []
    for block in official_blocks:
        scratch_block = scratch_blocks_by_id.get(block.id)
        if scratch_block is None:
            changes.append(
                {
                    "file": file_path,
                    "targetBlockId": block.id,
                    "operation": "delete_block",
                    "beforeHash": block.before_hash,
                    "afterText": "",
                    "reason": "自主工作流在隔离副本中删除了该段；仍需运行时差异审核后才能写入。",
                }
            )
            continue
        cleaned = scratch_block.text.strip()
        if cleaned != block.text.strip():
            changes.append(
                {
                    "file": file_path,
                    "targetBlockId": block.id,
                    "operation": "replace_block",
                    "beforeHash": block.before_hash,
                    "afterText": cleaned,
                    "reason": "自主工作流在隔离副本中完成本段修订；正式写入必须经过差异审核。",
                }
            )
    official_ids = {block.id for block in official_blocks}
    new_blocks = [block for block in scratch_blocks_by_id.values() if block.id not in official_ids]
    if new_blocks and official_blocks:
        anchor = official_blocks[-1]
        tail_text = "\n\n".join(block.text.strip() for block in new_blocks if block.text.strip()).strip()
        if tail_text:
            changes.append(
                {
                    "file": file_path,
                    "targetBlockId": anchor.id,
                    "operation": "insert_after_block",
                    "beforeHash": anchor.before_hash,
                    "afterText": tail_text,
                    "reason": "自主工作流在隔离副本中新增了段落；作为可审核插入块追加到当前章节。",
                }
            )
    rules = [rule.id for rule in applicable_rules(official, file_path)[:5]]
    return {
        "id": f"PP-autonomous-{Path(file_path).stem}-{workflow.get('runId', 'run')}",
        "summary": f"自主写作工作流修订《{markdown_title(official.root, file_path)}》。",
        "sourceAnnotations": sources,
        "rulesUsed": rules,
        "changes": _mark_reviewed_changes(official, changes),
        "workflow": dict(workflow),
        "safety": {"impactScope": "current_chapter", "writePath": "PatchProposal", "scratchOnly": True},
    }


def _diagnostic_patch(context: ProjectContext, file_path: str, *, workflow: Mapping[str, Any], reason: str) -> Dict[str, Any]:
    sources = _autonomous_sources(context)
    return {
        "id": f"PP-autonomous-diagnostic-{Path(file_path).stem}-{workflow.get('runId', 'run')}",
        "summary": f"自主写作工作流失败诊断：《{markdown_title(context.root, file_path)}》没有生成可应用正文修改。",
        "sourceAnnotations": sources,
        "rulesUsed": [rule.id for rule in applicable_rules(context, file_path)[:5]],
        "changes": [],
        "workflow": {
            **dict(workflow),
            "source": "autonomous-workflow-diagnostic",
            "localFallback": True,
            "diagnosticOnly": True,
            "fallbackReason": reason,
            "runLog": "自主工作流没有产生可通过质量/安全门槛的隔离副本章节修改；本地只记录诊断，不生成模板正文。",
        },
        "safety": {
            "impactScope": "current_chapter",
            "writePath": "PatchProposal",
            "diagnosticOnly": True,
            "acceptDisabled": True,
        },
    }


def _diagnostic_response(
    *,
    runtime: Any,
    context: ProjectContext,
    file_path: str,
    run_id: str,
    run_dir: Path,
    artifacts_dir: Path,
    workflow: Mapping[str, Any],
    reason: str,
    codex_summary: Mapping[str, Any],
) -> Dict[str, Any]:
    patch = _diagnostic_patch(context, file_path, workflow=workflow, reason=reason)
    validation = runtime.validate_patch(patch)
    patch["workflow"]["patchValidation"] = validation
    _write_json(artifacts_dir / "diagnostic.json", {"reason": reason, "codex": dict(codex_summary), "validation": validation})
    _write_json(artifacts_dir / "diagnostic-patch-proposal.json", patch)
    artifacts = _artifact_list(context.root, run_dir)
    patch["workflow"]["artifacts"] = artifacts
    _write_json(artifacts_dir / "diagnostic-patch-proposal.json", patch)
    _write_jsonl(run_dir / "events.jsonl", {"type": "autonomous.run.diagnostic", "runId": run_id, "reason": reason})
    return {
        "runId": run_id,
        "skill": "autonomous-writing-workflow",
        "source": "autonomous-workflow-diagnostic",
        "workflow": patch["workflow"],
        "events": [
            {"type": "autonomous.run.started", "runId": run_id, "file": file_path},
            {"type": "autonomous.codex.completed", "summary": dict(codex_summary)},
            {"type": "autonomous.run.diagnostic", "reason": reason},
        ],
        "output": patch,
        "codex": dict(codex_summary),
        "fallbackFrom": {"source": workflow.get("source", "autonomous-codex-scratch"), "reason": reason},
        "artifacts": _artifact_list(context.root, run_dir),
    }


def _quality_report(official: ProjectContext, scratch: ProjectContext, file_path: str, patch: Mapping[str, Any]) -> Dict[str, Any]:
    before = "\n".join(block.text for block in (official.blocks.get(file_path) or {}).values())
    after = "\n".join(block.text for block in (scratch.blocks.get(file_path) or {}).values())
    changes = patch.get("changes") if isinstance(patch.get("changes"), list) else []
    changed_text = "\n".join(str(change.get("afterText", "")) for change in changes if isinstance(change, Mapping))
    return {
        "file": file_path,
        "beforeChars": manuscript_word_count(before),
        "afterChars": manuscript_word_count(after),
        "changedChars": manuscript_word_count(changed_text),
        "changedBlocks": len(changes),
        "totalBlocks": len(official.blocks.get(file_path) or {}),
        "h2Before": len(re.findall(r"^##\s+", before, flags=re.MULTILINE)),
        "h2After": len(re.findall(r"^##\s+", after, flags=re.MULTILINE)),
        "meaningful": _patch_is_meaningful(official, patch),
        "notes": "质量报告只做启发式检查；最终是否可提交由运行时修改引擎决定。",
    }


def _patch_is_meaningful(context: ProjectContext, patch: Mapping[str, Any]) -> bool:
    changes = patch.get("changes")
    if not isinstance(changes, list) or not changes:
        return False
    for change in changes:
        if not isinstance(change, Mapping):
            continue
        operation = str(change.get("operation", ""))
        if operation in {"insert_before_block", "insert_after_block", "delete_block"}:
            return True
        if operation == "replace_block":
            file_path = str(change.get("file", ""))
            block_id = str(change.get("targetBlockId", ""))
            block = (context.blocks.get(file_path) or {}).get(block_id)
            if block and str(change.get("afterText", "")).strip() != block.text.strip():
                return True
    return False


def _copy_project_to_scratch(root: Path, scratch_root: Path) -> None:
    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored = {".git", "__pycache__", ".pytest_cache", ".DS_Store"}.intersection(names)
        if Path(directory).name == ".bookai":
            ignored.add("runs")
        return ignored

    shutil.copytree(root, scratch_root, ignore=ignore)


def _copy_scratch_autonomous_artifacts(scratch_root: Path, artifacts_dir: Path) -> None:
    """Persist Codex-authored scratch artifacts for review/debug.

    BookWorkbench writes its own outer artifacts (prompt, model summary,
    validated proposal).  Autonomous Codex may also write run plans, rule
    deltas, revision logs, or quality notes inside ``scratch/.bookai``.  Copy
    those into the official run artifact directory with a prefix so the user can
    inspect the model's real workflow without treating scratch files as
    authoritative project metadata.
    """

    source = scratch_root / ".bookai" / "autonomous"
    if not source.exists() or not source.is_dir():
        return
    for path in sorted(item for item in source.rglob("*") if item.is_file()):
        if path.stat().st_size > 1_000_000:
            continue
        relative = path.relative_to(source).as_posix().replace("/", "__")
        target = artifacts_dir / f"scratch-{relative}"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _ensure_runs_git_ignored(root: Path) -> None:
    git_dir = root / ".git"
    if git_dir.exists() and git_dir.is_dir():
        info = git_dir / "info"
        info.mkdir(parents=True, exist_ok=True)
        exclude = info / "exclude"
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if f"{RUNS_DIR}/" not in existing:
            suffix = "" if existing.endswith("\n") or not existing else "\n"
            exclude.write_text(f"{existing}{suffix}{RUNS_DIR}/\n", encoding="utf-8")


def _scratch_approval_handler(scratch_root: Path) -> ApprovalHandler:
    scratch = scratch_root.resolve()

    def handler(message: Dict[str, Any]) -> Dict[str, Any]:
        method = message.get("method")
        params = message.get("params") if isinstance(message.get("params"), dict) else message
        if method == "item/fileChange/requestApproval":
            paths = _extract_file_change_paths(params)
            if paths and all(_path_is_inside_scratch(path, scratch) for path in paths):
                return {"decision": "accept", "reason": "scratch_file_change_allowed", "paths": paths}
            return {"decision": "decline", "reason": "file_change_outside_scratch", "paths": paths}
        if method == "item/commandExecution/requestApproval":
            return {"decision": "decline", "reason": "autonomous_mvp_declines_command_approval_requests"}
        if method == "item/permissions/requestApproval":
            return {"decision": "decline", "reason": "permission_escalation_denied_by_autonomous_mvp"}
        return {"decision": "decline", "reason": "unknown_appserver_request"}

    return handler


def _path_is_inside_scratch(path_value: str, scratch: Path) -> bool:
    if not isinstance(path_value, str) or not path_value:
        return False
    raw = Path(path_value)
    if ".." in raw.parts:
        return False
    candidate = raw if raw.is_absolute() else scratch / raw
    try:
        resolved = candidate.resolve()
    except OSError:
        return False
    return resolved == scratch or scratch in resolved.parents


def _extract_file_change_paths(event: object) -> list[str]:
    if not isinstance(event, Mapping):
        return []
    paths: list[str] = []
    for key in ("file", "path"):
        value = event.get(key)
        if isinstance(value, str):
            paths.append(value)
    changes = event.get("changes") or event.get("fileChanges") or event.get("files")
    if isinstance(changes, list):
        for item in changes:
            if isinstance(item, str):
                paths.append(item)
            elif isinstance(item, Mapping):
                for key in ("file", "path"):
                    value = item.get(key)
                    if isinstance(value, str):
                        paths.append(value)
    return sorted(set(paths))


def _run_plan_markdown(context: ProjectContext, file_path: str, *, goal: str, mode: str, run_id: str) -> str:
    rules = applicable_rules(context, file_path)
    rule_lines = "\n".join(f"- {rule.id}: {rule.text}" for rule in rules[:8]) or "- 无显式规则。"
    return (
        f"# 自主写作工作流运行计划\n\n"
        f"- Run: `{run_id}`\n"
        f"- 目标章节: `{file_path}` / 《{markdown_title(context.root, file_path)}》\n"
        f"- 模式: `{mode}`\n"
        f"- 用户目标: {goal or '按项目工作流自主修订当前章节'}\n\n"
        "## 安全边界\n\n"
        "智能服务只在 `.bookai/runs/<run>/scratch` 隔离副本内工作。正式 `chapters/*.md` 不会被直接写入；"
        "隔离副本差异必须转换成安全修改建议，经过运行时校验、差异审核和用户接受后才落地。\n\n"
        "## 本轮可用规则\n\n"
        f"{rule_lines}\n"
    )


def _autonomous_sources(context: ProjectContext) -> list[str]:
    sources = [AUTONOMOUS_SOURCE_ANNOTATION]
    if powerbook_workflow_available(context.root):
        sources.insert(0, POWERBOOK_SOURCE_ANNOTATION)
    return sources


def _artifact_list(root: Path, run_dir: Path) -> list[Dict[str, Any]]:
    artifacts_dir = run_dir / "artifacts"
    items: list[Dict[str, Any]] = []
    if artifacts_dir.exists():
        for path in sorted(item for item in artifacts_dir.rglob("*") if item.is_file()):
            relative = path.relative_to(root).as_posix()
            label = _artifact_label(path.stem)
            items.append({"label": label, "path": relative, "kind": "file", "bytes": path.stat().st_size})
    return items


def _artifact_label(stem: str) -> str:
    labels = {
        "run-plan": "运行计划",
        "prompt": "提示词",
        "model-calls": "模型调用记录",
        "quality-report": "质量报告",
        "patch-proposal": "修改建议",
        "final-diff": "最终差异",
        "diagnostic": "失败诊断",
        "diagnostic-patch-proposal": "诊断修改建议",
        "rejected-patch-proposal": "被拒绝的修改建议",
        "scratch-diff": "隔离副本差异",
        "scratch-run-plan": "隔离副本运行计划",
        "scratch-rules-delta": "隔离副本规则变化",
        "scratch-revision-log": "隔离副本修订日志",
        "scratch-quality-report": "隔离副本质量报告",
    }
    return labels.get(stem, stem.replace("-", " "))


def _safe_run_dir(root: Path, run_id: str) -> Path:
    if not re.match(r"^RUN-[A-Za-z0-9_.-]+$", run_id or ""):
        raise ValueError("Invalid autonomous run id.")
    run_dir = (root / RUNS_DIR / run_id).resolve()
    runs_root = (root / RUNS_DIR).resolve()
    if run_dir != runs_root and runs_root in run_dir.parents and run_dir.exists():
        return run_dir
    raise ValueError(f"Unknown autonomous run id: {run_id}")


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"RUN-{stamp}-{int(time.time_ns() % 1_000_000):06d}"


def _write_artifact(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _read_optional(path: Path, limit: int) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[:limit]


def _read_revision_logs(root: Path, limit: int) -> str:
    logs_dir = root / "reviews" / "resolved"
    if not logs_dir.exists():
        return ""
    chunks: list[str] = []
    total = 0
    for path in sorted(logs_dir.glob("*.md")):
        remaining = max(0, limit - total)
        if remaining <= 0:
            break
        text = _read_optional(path, remaining)
        if not text:
            continue
        chunk = f"\n\n--- {path.relative_to(root).as_posix()} ---\n{text}"
        chunks.append(chunk)
        total += len(chunk)
    return "".join(chunks)[:limit]


def _normalized_chapter_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")



def _unified_text_diff(before: str, after: str, *, file_path: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
        )
    )

def _validation_issue_summary(validation: object) -> str:
    if not isinstance(validation, Mapping):
        return ""
    issues = validation.get("issues")
    if not isinstance(issues, list):
        return ""
    messages: list[str] = []
    for issue in issues:
        if isinstance(issue, Mapping):
            messages.append(str(issue.get("message") or issue.get("code") or ""))
        else:
            messages.append(str(issue))
    return "；".join(item for item in messages[:3] if item)


def _mark_reviewed_changes(context: ProjectContext, changes: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    marked: list[Dict[str, Any]] = []
    for change in changes:
        item = dict(change)
        if context.status_for_file(str(item.get("file", ""))) == "reviewed":
            item["requiresSecondaryApproval"] = True
        marked.append(item)
    return marked
