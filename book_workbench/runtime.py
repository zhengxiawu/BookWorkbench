"""Runtime orchestration for skill-like manuscript operations."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .annotation_engine import annotation_to_dict, classification_summary, open_annotations
from .audit import AuditLog, utc_now
from .patch_engine import apply_patch, make_annotation_patch, preview_diff, validate_patch
from .project import load_project
from .rule_engine import applicable_rules, propose_rules_from_annotations, rule_to_dict
from .skill_manager import build_skill_roots, discover_skills, resolve_skills


class RuntimeErrorBase(RuntimeError):
    pass


class UnknownSkillError(RuntimeErrorBase):
    pass


class RuntimeOrchestrator:
    def __init__(
        self,
        project_root: str | Path,
        *,
        builtin_skills_root: str | Path | None = None,
        audit_path: str | Path | None = None,
        write_audit: bool = True,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.context = load_project(self.project_root)
        roots = build_skill_roots(project_root=self.project_root, builtin_root=builtin_skills_root)
        self.skills = resolve_skills(discover_skills(roots))
        self.audit = AuditLog(self.project_root, path=audit_path)
        self.write_audit = write_audit

    def _reload_context(self):
        self.context = load_project(self.project_root)
        return self.context

    def _event(self, event_type: str, **payload: object) -> Dict[str, object]:
        return {"type": event_type, "timestamp": utc_now(), **payload}

    def _audit(self, event: Dict[str, object]) -> None:
        if self.write_audit:
            self.audit.append(event)

    def run_skill(
        self,
        skill_name: str,
        *,
        annotation_ids: Optional[Iterable[str]] = None,
        scope_file: Optional[str] = None,
    ) -> Dict[str, object]:
        self._reload_context()
        if self.skills and skill_name not in self.skills:
            raise UnknownSkillError(f"Unknown skill: {skill_name}")
        if skill_name not in {"revise-with-annotations", "extract-writing-rules", "propagate-rules"}:
            raise UnknownSkillError(f"Runtime MVP does not implement skill: {skill_name}")

        run_id = f"run-{uuid.uuid4().hex[:12]}"
        events = [self._event("run.started", runId=run_id), self._event("skill.started", runId=run_id, skillName=skill_name)]
        self._audit({"type": "run.started", "runId": run_id, "skill": skill_name})

        annotations = self._select_annotations(annotation_ids=annotation_ids, scope_file=scope_file)
        events.append(
            self._event(
                "agent.message",
                runId=run_id,
                message=f"读取批注 {len(annotations)} 条，分类 {classification_summary(annotations)}。",
            )
        )

        if skill_name == "revise-with-annotations":
            output = self._run_revise(annotations)
            events.append(self._event("patch.ready", runId=run_id, patchId=output.get("id", "")))
        elif skill_name == "extract-writing-rules":
            output = propose_rules_from_annotations(self.context, annotations)
            events.append(self._event("agent.message", runId=run_id, message="已生成 RuleProposal。"))
        else:
            output = self._run_propagate(annotations)
            events.append(self._event("agent.message", runId=run_id, message="已生成按章节分组的 PatchProposal 列表。"))

        events.append(self._event("run.completed", runId=run_id, summary=f"{skill_name} completed"))
        self._audit({"type": "run.completed", "runId": run_id, "skill": skill_name})
        return {"runId": run_id, "skill": skill_name, "events": events, "output": output}

    def _patch_id(self, patch: object) -> str:
        if isinstance(patch, dict) and isinstance(patch.get("id"), str):
            return patch["id"]
        return ""

    def _patch_files(self, patch: object) -> List[str]:
        if not isinstance(patch, dict) or not isinstance(patch.get("changes"), list):
            return []
        return sorted(
            {
                change["file"]
                for change in patch["changes"]
                if isinstance(change, dict) and isinstance(change.get("file"), str)
            }
        )

    def validate_patch(self, patch: object, *, allow_reviewed: bool = False) -> Dict[str, object]:
        context = self._reload_context()
        result = validate_patch(context, patch, allow_reviewed=allow_reviewed)
        return {
            "valid": result.valid,
            "issues": [issue.__dict__ for issue in result.issues],
        }

    def preview_patch(self, patch: object, *, allow_reviewed: bool = False) -> Dict[str, object]:
        context = self._reload_context()
        result = validate_patch(context, patch, allow_reviewed=allow_reviewed)
        validation = {
            "valid": result.valid,
            "issues": [issue.__dict__ for issue in result.issues],
        }
        if not result.valid:
            self._audit({"type": "patch.rejected", "patchId": self._patch_id(patch), "issues": validation["issues"]})
            return {"validation": validation, "diff": ""}
        files = self._patch_files(patch)
        diff = preview_diff(context, patch)
        self._audit({"type": "patch.previewed", "patchId": self._patch_id(patch), "files": files})
        return {"validation": validation, "diff": diff}

    def accept_patch(self, patch: object, *, allow_reviewed: bool = False) -> Dict[str, object]:
        context = self._reload_context()
        result = apply_patch(
            context,
            patch,
            allow_reviewed=allow_reviewed,
        )
        validation = {
            "valid": result.valid,
            "issues": [issue.__dict__ for issue in result.issues],
        }
        if result.valid:
            files = self._patch_files(patch)
            self._audit({"type": "patch.applied", "patchId": self._patch_id(patch), "files": files})
            self._reload_context()
        else:
            self._audit({"type": "patch.rejected", "patchId": self._patch_id(patch), "issues": validation["issues"]})
        return {
            "validation": validation,
            "applied": result.valid,
        }

    def _select_annotations(
        self,
        *,
        annotation_ids: Optional[Iterable[str]],
        scope_file: Optional[str],
    ):
        annotations = open_annotations(self.context, file_path=scope_file)
        if annotation_ids:
            wanted = set(annotation_ids)
            annotations = [item for item in annotations if item.id in wanted]
        return annotations

    def _run_revise(self, annotations) -> Dict[str, object]:
        if not annotations:
            return {"id": "PP-empty", "summary": "没有可处理的 open 批注。", "sourceAnnotations": [], "changes": []}
        patch = make_annotation_patch(self.context, annotations[0].id)
        result = validate_patch(self.context, patch)
        patch["validation"] = {
            "valid": result.valid,
            "issues": [issue.__dict__ for issue in result.issues],
        }
        return patch

    def _run_propagate(self, annotations) -> Dict[str, object]:
        proposals: Dict[str, List[Dict[str, object]]] = {}
        excluded: List[Dict[str, str]] = []
        for annotation in annotations:
            status = self.context.status_for_file(annotation.file)
            if status not in {"draft", "unreviewed"}:
                excluded.append({"file": annotation.file, "status": status, "annotation": annotation.id})
                continue
            if not applicable_rules(self.context, annotation.file):
                excluded.append({"file": annotation.file, "status": status, "annotation": annotation.id})
                continue
            patch = make_annotation_patch(self.context, annotation.id)
            proposals.setdefault(annotation.file, []).append(patch)
        return {"patchProposalsByChapter": proposals, "excluded": excluded}

    def inspect(self) -> Dict[str, object]:
        context = self._reload_context()
        return {
            "root": self.project_root.as_posix(),
            "skills": {name: skill.to_dict() for name, skill in sorted(self.skills.items())},
            "annotations": [annotation_to_dict(item) for item in context.annotations],
            "rules": [rule_to_dict(rule) for rule in context.rules],
            "chapterStatus": context.chapter_status,
            "blocks": {file: sorted(blocks) for file, blocks in context.blocks.items()},
        }
