"""Runtime orchestration for skill-like manuscript operations."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .annotation_engine import annotation_to_dict, classification_summary, open_annotations
from .audit import AuditLog, utc_now
from .git_service import GitError, amend_all, commit_all, ensure_repo
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

    def refreshed_context(self):
        """Reload and return the current project context for read-only prompt construction."""

        return self._reload_context()

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
        commit_error = None
        if result.valid:
            files = self._patch_files(patch)
            self._audit({"type": "patch.applied", "patchId": self._patch_id(patch), "files": files})
            try:
                ensure_repo(self.project_root)
                commit_all(
                    self.project_root,
                    self._commit_message(patch, files),
                )
            except GitError as exc:
                commit_error = str(exc)
                self._audit({"type": "git.commit_skipped", "patchId": self._patch_id(patch), "reason": commit_error})
            else:
                self._audit({"type": "git.committed", "patchId": self._patch_id(patch), "files": files})
                try:
                    amend_all(self.project_root)
                except GitError as exc:
                    commit_error = str(exc)
                    self._audit({"type": "git.audit_amend_failed", "patchId": self._patch_id(patch), "reason": commit_error})
            self._reload_context()
        else:
            self._audit({"type": "patch.rejected", "patchId": self._patch_id(patch), "issues": validation["issues"]})
        return {
            "validation": validation,
            "applied": result.valid,
            "commitError": commit_error,
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

    def _commit_message(self, patch: object, files: List[str]) -> str:
        patch_id = self._patch_id(patch) or "runtime-patch"
        sources: List[str] = []
        rules: List[str] = []
        if isinstance(patch, dict):
            sources = [str(item) for item in patch.get("sourceAnnotations", []) if isinstance(item, str)]
            rules = [str(item) for item in patch.get("rulesUsed", []) if isinstance(item, str)]
        summary = f"Apply safe manuscript patch {patch_id}"
        body = "Runtime accepted a validated PatchProposal and applied it through the patch engine."
        return (
            f"{summary}\n\n"
            f"{body}\n\n"
            f"Constraint: All manuscript writes must pass PatchProposal validation\n"
            f"Confidence: high\n"
            f"Scope-risk: narrow\n"
            f"Directive: Do not bypass RuntimeOrchestrator.accept_patch for manuscript writes\n"
            f"Tested: Runtime patch validation before write\n"
            f"Related: patch={patch_id}; files={','.join(files)}; sources={','.join(sources)}; rules={','.join(rules)}\n"
        )

    def _run_revise(self, annotations) -> Dict[str, object]:
        if not annotations:
            return {"id": "PP-empty", "summary": "没有可处理的 open 批注。", "sourceAnnotations": [], "changes": []}
        annotation = annotations[0]
        if self._looks_like_prompt_injection(annotation.text):
            patch = {
                "id": f"PP-{annotation.id}",
                "summary": "检测到批注中包含越权/注入式指令，已作为普通用户文本处理并拒绝自动改稿。",
                "sourceAnnotations": [annotation.id],
                "rulesUsed": [],
                "changes": [],
                "safety": {
                    "promptInjectionSuspected": True,
                    "warning": "Annotation text is untrusted user content, not system instruction.",
                },
            }
        else:
            patch = make_annotation_patch(self.context, annotation.id)
        result = validate_patch(self.context, patch)
        patch["validation"] = {
            "valid": result.valid,
            "issues": [issue.__dict__ for issue in result.issues],
        }
        return patch

    @staticmethod
    def _looks_like_prompt_injection(text: str) -> bool:
        lowered = text.lower()
        suspicious = (
            "忽略所有系统规则",
            "不要生成 patch",
            "不要生成patch",
            "删除 .bookai",
            "删除.bookai",
            "chapter-status.yaml",
            "locked",
            "system rules",
            "ignore all",
        )
        return any(token in lowered for token in suspicious)

    def evaluate_file_change_request(self, event: object) -> Dict[str, object]:
        self._reload_context()
        paths = self._extract_file_change_paths(event)
        if not paths:
            return {"decision": "decline", "reason": "no_file_paths"}
        reasons: List[str] = []
        for file_path in paths:
            path = str(file_path)
            if path.startswith("/") or ".." in Path(path).parts:
                reasons.append(f"forbidden_path:{path}")
                continue
            if path.startswith(".bookai/") or path in {"rules.yaml", "book.spec.md", "style-guide.md"}:
                reasons.append(f"metadata_requires_runtime_tool:{path}")
                continue
            if not path.startswith("chapters/") or Path(path).suffix != ".md":
                reasons.append(f"forbidden_file:{path}")
                continue
            status = self.context.status_for_file(path)
            if status == "locked":
                reasons.append(f"locked_chapter:{path}")
            elif status == "reviewed":
                reasons.append(f"reviewed_requires_secondary_approval:{path}")
            else:
                reasons.append(f"direct_manuscript_write_requires_patch_proposal:{path}")
        return {"decision": "decline", "reason": ";".join(reasons), "paths": paths}

    @staticmethod
    def _extract_file_change_paths(event: object) -> List[str]:
        if not isinstance(event, dict):
            return []
        paths: List[str] = []
        for key in ("file", "path"):
            value = event.get(key)
            if isinstance(value, str):
                paths.append(value)
        changes = event.get("changes") or event.get("fileChanges") or event.get("files")
        if isinstance(changes, list):
            for item in changes:
                if isinstance(item, str):
                    paths.append(item)
                elif isinstance(item, dict):
                    for key in ("file", "path"):
                        value = item.get(key)
                        if isinstance(value, str):
                            paths.append(value)
        return sorted(set(paths))

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
            "bookSpec": context.book_spec,
            "styleGuide": context.style_guide,
            "skills": {name: skill.to_dict() for name, skill in sorted(self.skills.items())},
            "annotations": [annotation_to_dict(item) for item in context.annotations],
            "rules": [rule_to_dict(rule) for rule in context.rules],
            "chapterStatus": context.chapter_status,
            "blocks": {file: sorted(blocks) for file, blocks in context.blocks.items()},
        }
