"""PatchProposal validation, diff preview, and safe application."""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

from .models import ProjectContext, ValidationIssue, ValidationResult

MANUSCRIPT_FILE_PREFIX = "chapters/"


class PatchError(RuntimeError):
    pass


def load_patch(path: str | Path) -> Dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_patch(context: ProjectContext, patch: Dict, *, allow_reviewed: bool = False) -> ValidationResult:
    issues: List[ValidationIssue] = []
    touches_locked = False
    touches_reviewed = False

    for field in ("id", "sourceAnnotations", "changes", "summary"):
        if field not in patch:
            issues.append(ValidationIssue("missing_field", f"PatchProposal is missing required field: {field}"))

    source_annotations = patch.get("sourceAnnotations", [])
    if not source_annotations:
        issues.append(
            ValidationIssue(
                "missing_sources",
                "PatchProposal must cite at least one source annotation or explicit user instruction.",
            )
        )

    known_annotation_ids = {annotation.id for annotation in context.annotations}
    for annotation_id in source_annotations:
        if annotation_id.startswith("USER-") or annotation_id in known_annotation_ids:
            continue
        issues.append(
            ValidationIssue(
                "unknown_source_annotation",
                f"Unknown source annotation: {annotation_id}",
            )
        )

    changes = patch.get("changes", [])
    if not isinstance(changes, list) or not changes:
        issues.append(ValidationIssue("empty_changes", "PatchProposal must include at least one change."))
        changes = []

    for index, change in enumerate(changes):
        prefix = f"changes[{index}]"
        for field in ("file", "targetBlockId", "operation", "beforeHash", "afterText", "reason"):
            if field not in change:
                issues.append(ValidationIssue("missing_change_field", f"{prefix} is missing required field: {field}"))
        file_path = change.get("file")
        block_id = change.get("targetBlockId")
        operation = change.get("operation")
        before_hash = change.get("beforeHash")
        after_text = change.get("afterText", "")

        if operation not in {"replace_block", "insert_before_block", "insert_after_block", "delete_block"}:
            issues.append(ValidationIssue("invalid_operation", f"{prefix} has invalid operation: {operation}"))

        if not isinstance(file_path, str) or not file_path.startswith(MANUSCRIPT_FILE_PREFIX):
            issues.append(
                ValidationIssue(
                    "forbidden_file",
                    f"{prefix} targets a forbidden file. MVP patching is limited to chapters/*.md.",
                )
            )
            continue

        status = context.status_for_file(file_path)
        if status == "locked":
            touches_locked = True
            issues.append(ValidationIssue("locked_chapter", f"{prefix} targets locked chapter: {file_path}"))
        if status == "reviewed":
            touches_reviewed = True
            if not allow_reviewed:
                issues.append(
                    ValidationIssue(
                        "reviewed_chapter_requires_secondary_approval",
                        f"{prefix} targets reviewed chapter without secondary approval: {file_path}",
                    )
                )
            if change.get("requiresSecondaryApproval") is not True:
                issues.append(
                    ValidationIssue(
                        "reviewed_change_not_marked",
                        f"{prefix} must set requiresSecondaryApproval=true for reviewed chapter: {file_path}",
                    )
                )

        file_blocks = context.blocks.get(file_path)
        if not file_blocks:
            issues.append(ValidationIssue("unknown_file", f"{prefix} targets unknown file: {file_path}"))
            continue
        block = file_blocks.get(str(block_id))
        if not block:
            issues.append(ValidationIssue("unknown_block", f"{prefix} targets unknown block: {block_id}"))
            continue
        if before_hash != block.before_hash:
            issues.append(
                ValidationIssue(
                    "hash_mismatch",
                    f"{prefix} beforeHash {before_hash!r} does not match current block hash {block.before_hash!r}.",
                )
            )
        if "mw:block" in str(after_text):
            issues.append(
                ValidationIssue(
                    "anchor_in_after_text",
                    f"{prefix} afterText must not contain block anchors; anchors are preserved by the patch engine.",
                )
            )

    return ValidationResult(
        valid=not any(issue.severity == "error" for issue in issues),
        issues=issues,
        touches_locked=touches_locked,
        touches_reviewed=touches_reviewed,
    )


def _replacement_lines(anchor: str, after_text: str, *, trailing_blank: bool = False) -> List[str]:
    body_lines = after_text.splitlines() if after_text else []
    replacement = [anchor, *body_lines]
    if trailing_blank:
        replacement.append("")
    return replacement


def _apply_change_to_lines(context: ProjectContext, lines: List[str], change: Dict, line_offset: int) -> tuple[List[str], int]:
    block = context.block(change["file"], change["targetBlockId"])
    start = block.anchor_line - 1 + line_offset
    end = block.end_line + line_offset
    original_had_trailing_blank = end > start and lines[end - 1] == ""
    replacement = _replacement_lines(
        block.anchor,
        change.get("afterText", ""),
        trailing_blank=original_had_trailing_blank,
    )

    operation = change["operation"]
    if operation == "replace_block":
        new_lines = [*lines[:start], *replacement, *lines[end:]]
        return new_lines, len(replacement) - (end - start)
    if operation == "delete_block":
        new_lines = [*lines[:start], *lines[end:]]
        return new_lines, -(end - start)
    if operation == "insert_before_block":
        new_lines = [*lines[:start], *replacement, "", *lines[start:]]
        return new_lines, len(replacement) + 1
    if operation == "insert_after_block":
        new_lines = [*lines[:end], "", *replacement, *lines[end:]]
        return new_lines, len(replacement) + 1
    raise PatchError(f"Unsupported operation: {operation}")


def proposed_file_text(context: ProjectContext, patch: Dict, file_path: str) -> str:
    full_path = context.root / file_path
    lines = full_path.read_text(encoding="utf-8").splitlines()
    file_changes = [change for change in patch.get("changes", []) if change.get("file") == file_path]
    file_changes.sort(key=lambda change: context.block(file_path, change["targetBlockId"]).anchor_line)
    offset = 0
    for change in file_changes:
        lines, delta = _apply_change_to_lines(context, lines, change, offset)
        offset += delta
    return "\n".join(lines) + "\n"


def preview_diff(context: ProjectContext, patch: Dict) -> str:
    files = sorted({change["file"] for change in patch.get("changes", [])})
    hunks: List[str] = []
    for file_path in files:
        original = (context.root / file_path).read_text(encoding="utf-8")
        proposed = proposed_file_text(context, patch, file_path)
        hunks.extend(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                proposed.splitlines(keepends=True),
                fromfile=f"a/{file_path}",
                tofile=f"b/{file_path}",
            )
        )
    return "".join(hunks)


def apply_patch(context: ProjectContext, patch: Dict, *, allow_reviewed: bool = False) -> ValidationResult:
    result = validate_patch(context, patch, allow_reviewed=allow_reviewed)
    if not result.valid:
        return result
    for file_path in sorted({change["file"] for change in patch.get("changes", [])}):
        (context.root / file_path).write_text(proposed_file_text(context, patch, file_path), encoding="utf-8")
    return result


def make_annotation_patch(context: ProjectContext, annotation_id: str) -> Dict:
    """Generate a conservative demo patch for a style annotation.

    This is intentionally deterministic and narrow: it only handles the sample
    "show through action, not inner explanation" annotation. In real app-server
    usage, Codex would produce PatchProposal JSON and this module would validate
    and preview/apply it.
    """

    annotation = next((item for item in context.annotations if item.id == annotation_id), None)
    if not annotation:
        raise PatchError(f"Unknown annotation: {annotation_id}")
    block = context.block(annotation.file, annotation.block_id)
    status = context.status_for_file(annotation.file)
    rules = [
        rule
        for rule in context.rules
        if rule.status == "active"
        and (not rule.apply_to or status in rule.apply_to)
        and status not in rule.exclude
    ]
    if not rules:
        rules_used: List[str] = []
    else:
        rules_used = [rules[0].id]

    after_text = "我坐在审讯室里，盯着对面的男人。他没有看我，只把纸杯沿一点点捏扁。"
    return {
        "id": f"PP-{annotation.id}",
        "summary": f"按 {annotation.id} 将直接心理说明改为动作呈现。",
        "sourceAnnotations": [annotation.id],
        "rulesUsed": rules_used,
        "changes": [
            {
                "file": annotation.file,
                "targetBlockId": annotation.block_id,
                "operation": "replace_block",
                "beforeHash": block.before_hash,
                "afterText": after_text,
                "reason": "回应批注要求，避免直接解释内心，改用可见动作表现沉默与压力。",
                "requiresSecondaryApproval": status == "reviewed",
            }
        ],
        "safety": {
            "touchesLockedChapter": status == "locked",
            "touchesReviewedChapter": status == "reviewed",
            "impactScope": "single_block",
        },
    }
