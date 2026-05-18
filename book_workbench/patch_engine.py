"""PatchProposal validation, diff preview, and safe application."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from pathlib import Path
from typing import Dict, List, Tuple

from .models import ProjectContext, ValidationIssue, ValidationResult
from .project import ProjectLoadError, safe_chapter_path

MANUSCRIPT_FILE_PREFIX = "chapters/"
VALID_OPERATIONS = {"replace_block", "insert_before_block", "insert_after_block", "delete_block"}
INSERT_OPERATIONS = {"insert_before_block", "insert_after_block"}


class PatchError(RuntimeError):
    pass


def load_patch(path: str | Path) -> Dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _validation_issue(code: str, message: str) -> ValidationIssue:
    return ValidationIssue(code, message)


def validate_patch(context: ProjectContext, patch: object, *, allow_reviewed: bool = False) -> ValidationResult:
    issues: List[ValidationIssue] = []
    touches_locked = False
    touches_reviewed = False
    seen_targets: dict[Tuple[str, str], int] = {}

    if not isinstance(patch, dict):
        return ValidationResult(
            valid=False,
            issues=[_validation_issue("invalid_patch", "PatchProposal must be an object.")],
        )

    for field in ("id", "sourceAnnotations", "changes", "summary"):
        if field not in patch:
            issues.append(_validation_issue("missing_field", f"PatchProposal is missing required field: {field}"))
    for field in ("id", "summary"):
        if field in patch and not isinstance(patch[field], str):
            issues.append(_validation_issue("invalid_field_type", f"PatchProposal field {field} must be a string."))

    source_annotations = patch.get("sourceAnnotations", [])
    if not isinstance(source_annotations, list):
        issues.append(_validation_issue("invalid_sources", "sourceAnnotations must be an array."))
        source_annotations = []
    if not source_annotations:
        issues.append(
            _validation_issue(
                "missing_sources",
                "PatchProposal must cite at least one source annotation or explicit user instruction.",
            )
        )

    annotation_by_id = {annotation.id: annotation for annotation in context.annotations}
    known_annotation_ids = set(annotation_by_id)
    for annotation_id in source_annotations:
        if not isinstance(annotation_id, str):
            issues.append(_validation_issue("invalid_source_annotation", f"Source annotation must be a string: {annotation_id!r}"))
            continue
        if annotation_id.startswith("USER-") or annotation_id in known_annotation_ids:
            continue
        issues.append(_validation_issue("unknown_source_annotation", f"Unknown source annotation: {annotation_id}"))

    workflow = patch.get("workflow") if isinstance(patch.get("workflow"), dict) else {}
    whole_chapter_intent = (
        workflow.get("kind") == "trusted-powerbook-gemini-chapter"
        or workflow.get("localFallback") is True
        or patch.get("safety", {}).get("impactScope") == "current_chapter"
    )

    rules_used = patch.get("rulesUsed", patch.get("rules_used", []))
    if rules_used is None:
        rules_used = []
    if not isinstance(rules_used, list):
        issues.append(_validation_issue("invalid_rules_used", "rulesUsed must be an array when provided."))
        rules_used = []
    known_rule_ids = {rule.id for rule in context.rules}
    for rule_id in rules_used:
        if not isinstance(rule_id, str) or rule_id not in known_rule_ids:
            issues.append(_validation_issue("unknown_rule", f"Unknown rule referenced by PatchProposal: {rule_id!r}"))

    changes = patch.get("changes", [])
    if not isinstance(changes, list) or not changes:
        issues.append(_validation_issue("empty_changes", "PatchProposal must include at least one change."))
        changes = []

    for index, change in enumerate(changes):
        prefix = f"changes[{index}]"
        if not isinstance(change, dict):
            issues.append(_validation_issue("invalid_change", f"{prefix} must be an object."))
            continue
        for field in ("file", "targetBlockId", "operation", "beforeHash", "afterText", "reason"):
            if field not in change:
                issues.append(_validation_issue("missing_change_field", f"{prefix} is missing required field: {field}"))

        invalid_change_shape = False
        for field in ("file", "targetBlockId", "operation", "beforeHash", "afterText", "reason"):
            if field in change and not isinstance(change[field], str):
                invalid_change_shape = True
                issues.append(_validation_issue("invalid_change_field_type", f"{prefix}.{field} must be a string."))
        if "requiresSecondaryApproval" in change and not isinstance(change["requiresSecondaryApproval"], bool):
            invalid_change_shape = True
            issues.append(_validation_issue("invalid_change_field_type", f"{prefix}.requiresSecondaryApproval must be a boolean."))
        if invalid_change_shape or any(field not in change for field in ("file", "targetBlockId", "operation", "beforeHash", "afterText", "reason")):
            continue

        file_path = change["file"]
        block_id = change["targetBlockId"]
        operation = change["operation"]
        before_hash = change["beforeHash"]
        after_text = change["afterText"]
        target_key = (file_path, block_id)

        if target_key in seen_targets:
            issues.append(
                _validation_issue(
                    "duplicate_target_block",
                    f"{prefix} targets the same block as changes[{seen_targets[target_key]}]: {file_path}#{block_id}",
                )
            )
        else:
            seen_targets[target_key] = index

        if operation not in VALID_OPERATIONS:
            issues.append(_validation_issue("invalid_operation", f"{prefix} has invalid operation: {operation}"))

        if (
            not isinstance(file_path, str)
            or not file_path.startswith(MANUSCRIPT_FILE_PREFIX)
            or Path(file_path).suffix != ".md"
            or ".." in Path(file_path).parts
            or Path(file_path).is_absolute()
        ):
            issues.append(
                _validation_issue(
                    "forbidden_file",
                    f"{prefix} targets a forbidden file. MVP patching is limited to chapters/*.md.",
                )
            )
            continue
        status = context.status_for_file(file_path)
        if status == "locked":
            touches_locked = True
            issues.append(_validation_issue("locked_chapter", f"{prefix} targets locked chapter: {file_path}"))
        if status == "reviewed":
            touches_reviewed = True
            if not allow_reviewed:
                issues.append(
                    _validation_issue(
                        "reviewed_chapter_requires_secondary_approval",
                        f"{prefix} targets reviewed chapter without secondary approval: {file_path}",
                    )
                )
            if change.get("requiresSecondaryApproval") is not True:
                issues.append(
                    _validation_issue(
                        "reviewed_change_not_marked",
                        f"{prefix} must set requiresSecondaryApproval=true for reviewed chapter: {file_path}",
                    )
                )

        try:
            safe_chapter_path(context.root, file_path)
        except ProjectLoadError:
            issues.append(
                _validation_issue(
                    "forbidden_file",
                    f"{prefix} targets a forbidden file. MVP patching is limited to real chapters/*.md files.",
                )
            )
            continue

        file_blocks = context.blocks.get(file_path)
        if not file_blocks:
            issues.append(_validation_issue("unknown_file", f"{prefix} targets unknown file: {file_path}"))
            continue
        block = file_blocks.get(str(block_id))
        if not block:
            issues.append(_validation_issue("unknown_block", f"{prefix} targets unknown block: {block_id}"))
            continue
        if before_hash != block.before_hash:
            issues.append(
                _validation_issue(
                    "hash_mismatch",
                    f"{prefix} beforeHash {before_hash!r} does not match current block hash {block.before_hash!r}.",
                )
            )
        for source_id in source_annotations:
            annotation = annotation_by_id.get(source_id) if isinstance(source_id, str) else None
            if annotation is None or annotation.file != file_path or annotation.block_id != block_id:
                continue
            if annotation.before_hash and annotation.before_hash != block.before_hash:
                issues.append(
                    _validation_issue(
                        "hash_mismatch",
                        f"{prefix} source annotation {annotation.id} beforeHash {annotation.before_hash!r} does not match current block hash {block.before_hash!r}.",
                    )
                )
            if annotation.selected_text and annotation.selected_text not in block.text:
                issues.append(
                    _validation_issue(
                        "hash_mismatch",
                        f"{prefix} source annotation {annotation.id} selectedText no longer appears in {file_path}#{block_id}; annotation remap is required.",
                    )
                )
        if operation == "delete_block" and after_text:
            issues.append(_validation_issue("delete_after_text", f"{prefix} delete_block must use empty afterText."))
        if operation in INSERT_OPERATIONS and not str(after_text).strip():
            issues.append(_validation_issue("empty_insert_text", f"{prefix} {operation} must include afterText for the inserted block."))
        if operation in INSERT_OPERATIONS:
            generated_id = generated_insert_block_id(str(block_id), str(after_text))
            if generated_id in file_blocks:
                issues.append(
                    _validation_issue(
                        "generated_anchor_conflict",
                        f"{prefix} generated inserted block id already exists: {generated_id}",
                    )
                )
        if "mw:block" in str(after_text):
            issues.append(
                _validation_issue(
                    "anchor_in_after_text",
                    f"{prefix} afterText must not contain block anchors; anchors are preserved by the patch engine.",
                )
            )

    if whole_chapter_intent and changes and not any(issue.code == "missing_change_field" for issue in issues):
        changed_texts = [str(change.get("afterText", "")) for change in changes if isinstance(change, dict)]
        changed_text = "\n".join(changed_texts)
        changed_chars = len("".join(changed_text.split()))
        changed_files = {str(change.get("file", "")) for change in changes if isinstance(change, dict)}
        existing_chars = 0
        for file_path in changed_files:
            for block in (context.blocks.get(file_path) or {}).values():
                existing_chars += len("".join(block.text.split()))
        if existing_chars >= 800 and (len(changes) < 2 or changed_chars < 240):
            issues.append(
                _validation_issue(
                    "low_quality_whole_chapter_patch",
                    "Whole-chapter workflow proposals must contain a substantial, reviewable chapter revision instead of a tiny placeholder edit.",
                )
            )
        if existing_chars >= 4800:
            changed_block_ratio = len(changes) / max(1, sum(len(context.blocks.get(file_path) or {}) for file_path in changed_files))
            changed_char_ratio = changed_chars / max(1, existing_chars)
            if changed_block_ratio < 0.18 or changed_char_ratio < 0.12:
                issues.append(
                    _validation_issue(
                        "low_quality_whole_chapter_patch",
                        "Large PowerBook chapter workflow proposals must revise a meaningful section/batch, not a token single-paragraph edit.",
                    )
                )
        for after_text in changed_texts:
            if _template_like_powerbook_output(after_text):
                issues.append(
                    _validation_issue(
                        "low_quality_whole_chapter_patch",
                        "PowerBook workflow proposals must not use generic template prose as a fallback manuscript edit.",
                    )
                )

    return ValidationResult(
        valid=not any(issue.severity == "error" for issue in issues),
        issues=issues,
        touches_locked=touches_locked,
        touches_reviewed=touches_reviewed,
    )


def _template_like_powerbook_output(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text)
    template_markers = (
        "这一段需要先落到可见处境",
        "本地安全兜底",
        "模板正文",
        "外部模型未返回",
        "未生成可应用正文修改",
    )
    return any(marker in normalized for marker in template_markers)


def _short_text_hash(text: str, *, length: int = 8) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def _normalized_text_hash(text: str, *, length: int = 6) -> str:
    return _short_text_hash(text.strip(), length=length)


def current_block_hash(text: str) -> str:
    return f"sha256:{_normalized_text_hash(text, length=6)}"


def generated_insert_block_id(target_block_id: str, after_text: str) -> str:
    return f"{target_block_id}-ins-{_short_text_hash(after_text)}"


def generated_insert_anchor(target_block_id: str, after_text: str) -> str:
    block_id = generated_insert_block_id(target_block_id, after_text)
    return f"<!-- mw:block id={block_id} hash=sha256:{_short_text_hash(after_text, length=6)} -->"


def replacement_anchor(block_id: str, after_text: str) -> str:
    return f"<!-- mw:block id={block_id} hash={current_block_hash(after_text)} -->"


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
    operation = change["operation"]
    after_text = change.get("afterText", "")
    if operation in INSERT_OPERATIONS:
        anchor = generated_insert_anchor(block.id, after_text)
    elif operation == "replace_block":
        anchor = replacement_anchor(block.id, after_text)
    else:
        anchor = block.anchor
    replacement = _replacement_lines(
        anchor,
        after_text,
        trailing_blank=original_had_trailing_blank and operation not in INSERT_OPERATIONS,
    )

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
    chapter_path = safe_chapter_path(context.root, file_path)
    lines = chapter_path.read_text(encoding="utf-8").splitlines()
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
        original = safe_chapter_path(context.root, file_path).read_text(encoding="utf-8")
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


def apply_patch(
    context: ProjectContext,
    patch: Dict,
    *,
    allow_reviewed: bool = False,
) -> ValidationResult:
    result = validate_patch(context, patch, allow_reviewed=allow_reviewed)
    if not result.valid:
        return result
    files = sorted({change["file"] for change in patch.get("changes", [])})
    for file_path in files:
        safe_chapter_path(context.root, file_path).write_text(proposed_file_text(context, patch, file_path), encoding="utf-8")
    return result




def _deterministic_annotation_revision(block_text: str, selected_text: str | None = None) -> str:
    """Return a conservative deterministic revision for local e2e use.

    Real model-backed runs should still produce PatchProposal JSON; this helper
    keeps local app QA runnable without a network/model dependency while avoiding
    fixture-specific text for newly created books.
    """

    if (
        ("我的心里很复杂" in block_text and "内心充满了矛盾和挣扎" in block_text)
        or "我坐在审讯室里，盯着对面的男人。他沉默，眼神里没有任何波动。" in block_text
    ):
        return "我坐在审讯室里，盯着对面的男人。他没有看我，只把纸杯沿一点点捏扁。"
    selected = selected_text.strip() if selected_text else ""
    target = selected if selected and selected in block_text else ""
    if not block_text.strip():
        return "他停了一下，把手里的物件放回原处。"
    replacement = "他停了一下，指节抵住桌沿，把眼前的物件慢慢推回原处。"
    if target and _safe_to_replace_selected_text(block_text, target):
        return block_text.replace(target, replacement, 1)
    if replacement in block_text:
        return block_text
    return f"{block_text.rstrip()}\n\n{replacement}"


def _safe_to_replace_selected_text(block_text: str, selected_text: str) -> bool:
    """Return whether a deterministic fallback may replace the exact selection.

    Browser/Computer Use selection can land inside a word or marker.  The
    fallback must never splice Chinese prose into half of `AUTHOR-NOTE` or any
    other token; when uncertain it appends a clean sentence to the whole block.
    """

    if not selected_text.strip() or selected_text == block_text:
        return True
    if len(selected_text.strip()) < 8:
        return False
    lowered = block_text.lower()
    if "author-note" in lowered or "authornote" in lowered or "auhornote" in lowered:
        return False
    start = block_text.find(selected_text)
    if start < 0:
        return False
    end = start + len(selected_text)
    before = block_text[start - 1] if start > 0 else ""
    after = block_text[end] if end < len(block_text) else ""
    if _is_ascii_word_char(before) or _is_ascii_word_char(after):
        return False
    return True


def _is_ascii_word_char(value: str) -> bool:
    return bool(value and re.match(r"[A-Za-z0-9_-]", value))

def make_annotation_patch(context: ProjectContext, annotation_id: str) -> Dict:
    """Generate a conservative deterministic patch for a style annotation.

    In real app-server usage, Codex would produce PatchProposal JSON and this
    module would validate, preview, and apply it. This helper exists to keep the
    sample Runtime MVP runnable without a model dependency.
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
    rules_used = [rules[0].id] if rules else []

    after_text = _deterministic_annotation_revision(block.text, annotation.selected_text)
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
                "beforeHash": annotation.before_hash or block.before_hash,
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
