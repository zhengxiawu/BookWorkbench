from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from book_workbench.audit import AuditLog
from book_workbench.git_service import commit_all, ensure_repo, status
from book_workbench.patch_engine import apply_patch, make_annotation_patch, validate_patch
from book_workbench.project import index_markdown_blocks, load_project
from book_workbench.runtime import RuntimeOrchestrator
from book_workbench.skill_manager import discover_skills, resolve_skills

SAMPLE = ROOT / "manuscript_runtime_codex_appserver_v2" / "sample_project"
SKILLS = ROOT / "manuscript_runtime_codex_appserver_v2" / "skills"


class ExtendedRuntimeTests(unittest.TestCase):
    def copy_sample(self, tmp_path: Path) -> Path:
        target = tmp_path / "sample_project"
        shutil.copytree(SAMPLE, target)
        return target

    def patch_for(self, project: Path) -> dict:
        return make_annotation_patch(load_project(project), "AN-041")

    def assert_block_index_matches_chapter(self, project: Path, file_path: str, block_id: str) -> None:
        blocks = index_markdown_blocks(project, file_path)
        block_index = json.loads((project / ".bookai" / "block-index.json").read_text(encoding="utf-8"))
        self.assertEqual(
            block_index[file_path][block_id]["hash"],
            blocks[block_id].before_hash,
            f"{file_path}#{block_id} block-index hash must match embedded chapter anchor",
        )

    def test_forbidden_non_chapter_target_rejected(self) -> None:
        context = load_project(SAMPLE)
        patch = make_annotation_patch(context, "AN-041")
        patch["changes"][0]["file"] = "rules.yaml"

        result = validate_patch(context, patch)

        self.assertFalse(result.valid)
        self.assertTrue(any(issue.code == "forbidden_file" for issue in result.issues))


    def test_non_markdown_file_under_chapters_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.copy_sample(Path(tmp))
            notes = project / "chapters" / "notes.txt"
            notes.write_text(
                "<!-- mw:block id=notes-p001 hash=sha256:note -->\nnot a markdown chapter\n",
                encoding="utf-8",
            )
            annotations_path = project / ".bookai" / "annotations.jsonl"
            with annotations_path.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n"
                    + json.dumps(
                        {
                            "id": "AN-NOTE",
                            "file": "chapters/notes.txt",
                            "target": {"blockId": "notes-p001", "beforeHash": "sha256:note"},
                            "body": {"text": "do not edit txt", "type": "style", "priority": "high"},
                            "metadata": {"status": "open"},
                        },
                        ensure_ascii=False,
                    )
                )
            context = load_project(project)
            patch = {
                "id": "PP-notes",
                "summary": "must reject txt",
                "sourceAnnotations": ["AN-NOTE"],
                "changes": [
                    {
                        "file": "chapters/notes.txt",
                        "targetBlockId": "notes-p001",
                        "operation": "replace_block",
                        "beforeHash": "sha256:note",
                        "afterText": "edited",
                        "reason": "test non-md rejection",
                    }
                ],
            }

            result = validate_patch(context, patch)

            self.assertFalse(result.valid)
            self.assertTrue(any(issue.code == "forbidden_file" for issue in result.issues))

    def test_runtime_preview_and_accept_patch_are_audited_transactions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.copy_sample(Path(tmp))
            runtime = RuntimeOrchestrator(project)
            patch = make_annotation_patch(runtime.context, "AN-041")

            preview = runtime.preview_patch(patch)
            accepted = runtime.accept_patch(patch)
            events = AuditLog(project).read()
            text = (project / "chapters" / "ch05.md").read_text(encoding="utf-8")

            self.assertTrue(preview["validation"]["valid"], preview["validation"]["issues"])
            self.assertIn("纸杯沿一点点捏扁", preview["diff"])
            self.assertTrue(accepted["applied"], accepted)
            self.assertIn("纸杯沿一点点捏扁", text)
            self.assertIn("patch.previewed", [event["type"] for event in events])
            self.assertIn("patch.applied", [event["type"] for event in events])
            self.assertIn("git.committed", [event["type"] for event in events])
            self.assert_block_index_matches_chapter(project, "chapters/ch05.md", "ch05-p018")

    def test_accept_patch_commits_audit_events_and_leaves_clean_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.copy_sample(Path(tmp))
            ensure_repo(project)
            commit_all(project, "Initial fixture\n\nConfidence: high")
            runtime = RuntimeOrchestrator(project)
            patch = make_annotation_patch(runtime.context, "AN-041")
            before_count = int(subprocess.check_output(["git", "rev-list", "--count", "HEAD"], cwd=project, text=True).strip())

            accepted = runtime.accept_patch(patch)
            after_count = int(subprocess.check_output(["git", "rev-list", "--count", "HEAD"], cwd=project, text=True).strip())
            dirty = subprocess.check_output(["git", "status", "--short"], cwd=project, text=True).strip()
            show = subprocess.check_output(["git", "show", "--name-only", "--format=", "HEAD"], cwd=project, text=True)

            self.assertTrue(accepted["applied"], accepted)
            self.assertIsNone(accepted["commitError"])
            self.assertEqual(after_count, before_count + 1)
            self.assertEqual(dirty, "")
            self.assertIn(".bookai/audit-log.jsonl", show)
            self.assertIn(".bookai/block-index.json", show)
            self.assert_block_index_matches_chapter(project, "chapters/ch05.md", "ch05-p018")

    def test_runtime_accept_patch_rejects_and_audits_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.copy_sample(Path(tmp))
            runtime = RuntimeOrchestrator(project)
            patch = make_annotation_patch(runtime.context, "AN-041")
            patch["changes"][0]["beforeHash"] = "sha256:bad"
            chapter = project / "chapters" / "ch05.md"
            before = chapter.read_text(encoding="utf-8")

            rejected = runtime.accept_patch(patch)
            events = AuditLog(project).read()

            self.assertFalse(rejected["applied"])
            self.assertEqual(before, chapter.read_text(encoding="utf-8"))
            self.assertEqual(events[-1]["type"], "patch.rejected")

    def test_reviewed_chapter_requires_allow_and_mark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.copy_sample(Path(tmp))
            chapter = project / "chapters" / "ch02.md"
            chapter.write_text(
                "# 第二章\n\n<!-- mw:block id=ch02-p001 hash=sha256:222 -->\n旧文本。\n",
                encoding="utf-8",
            )
            context = load_project(project)
            patch = {
                "id": "PP-reviewed",
                "summary": "reviewed change",
                "sourceAnnotations": ["USER-manual"],
                "changes": [
                    {
                        "file": "chapters/ch02.md",
                        "targetBlockId": "ch02-p001",
                        "operation": "replace_block",
                        "beforeHash": "sha256:222",
                        "afterText": "新文本。",
                        "reason": "explicit user instruction",
                    }
                ],
            }

            rejected = validate_patch(context, patch)
            patch["changes"][0]["requiresSecondaryApproval"] = True
            accepted = validate_patch(context, patch, allow_reviewed=True)

            self.assertFalse(rejected.valid)
            self.assertTrue(any(issue.code == "reviewed_chapter_requires_secondary_approval" for issue in rejected.issues))
            self.assertTrue(accepted.valid, accepted.error_messages())

    def test_anchor_in_after_text_rejected(self) -> None:
        context = load_project(SAMPLE)
        patch = make_annotation_patch(context, "AN-041")
        patch["changes"][0]["afterText"] = "<!-- mw:block id=x hash=y -->\nbad"

        result = validate_patch(context, patch)

        self.assertFalse(result.valid)
        self.assertTrue(any(issue.code == "anchor_in_after_text" for issue in result.issues))

    def test_duplicate_target_block_rejected(self) -> None:
        context = load_project(SAMPLE)
        patch = make_annotation_patch(context, "AN-041")
        patch["changes"].append(dict(patch["changes"][0]))

        result = validate_patch(context, patch)

        self.assertFalse(result.valid)
        self.assertTrue(any(issue.code == "duplicate_target_block" for issue in result.issues))

    def test_malformed_patch_field_types_are_rejected(self) -> None:
        context = load_project(SAMPLE)
        patch = make_annotation_patch(context, "AN-041")
        patch["changes"][0]["afterText"] = ["not", "a", "string"]

        result = validate_patch(context, patch)

        self.assertFalse(result.valid)
        self.assertTrue(any(issue.code == "invalid_change_field_type" for issue in result.issues))

    def test_invalid_patch_does_not_mutate_file_and_audits_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.copy_sample(Path(tmp))
            context = load_project(project)
            patch = make_annotation_patch(context, "AN-041")
            patch["changes"][0]["beforeHash"] = "sha256:bad"
            chapter = project / "chapters" / "ch05.md"
            before = chapter.read_text(encoding="utf-8")

            result = RuntimeOrchestrator(project).accept_patch(patch)

            self.assertFalse(result["applied"])
            self.assertEqual(before, chapter.read_text(encoding="utf-8"))
            events = AuditLog(project).read()
            self.assertEqual(events[-1]["type"], "patch.rejected")

    def test_malformed_patch_does_not_crash_and_audits_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.copy_sample(Path(tmp))
            runtime = RuntimeOrchestrator(project)
            patch = make_annotation_patch(runtime.context, "AN-041")
            patch["changes"][0]["afterText"] = ["not", "a", "string"]
            chapter = project / "chapters" / "ch05.md"
            before = chapter.read_text(encoding="utf-8")

            result = runtime.accept_patch(patch)
            events = AuditLog(project).read()

            self.assertFalse(result["applied"])
            self.assertTrue(any(issue["code"] == "invalid_change_field_type" for issue in result["validation"]["issues"]))
            self.assertEqual(before, chapter.read_text(encoding="utf-8"))
            self.assertEqual(events[-1]["type"], "patch.rejected")

    @unittest.skipIf(not hasattr(os, "symlink"), "symlink unavailable on this platform")
    def test_symlinked_chapter_target_is_rejected_without_mutating_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.copy_sample(Path(tmp) / "project-root")
            outside = Path(tmp) / "outside.md"
            outside.write_text("<!-- mw:block id=evil-p001 hash=sha256:evil -->\noutside\n", encoding="utf-8")
            link = project / "chapters" / "evil.md"
            link.symlink_to(outside)
            annotations_path = project / ".bookai" / "annotations.jsonl"
            with annotations_path.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n"
                    + json.dumps(
                        {
                            "id": "AN-EVIL",
                            "file": "chapters/evil.md",
                            "target": {"blockId": "evil-p001", "beforeHash": "sha256:evil"},
                            "body": {"text": "do not escape", "type": "style", "priority": "high"},
                            "metadata": {"status": "open"},
                        },
                        ensure_ascii=False,
                    )
                )
            patch = {
                "id": "PP-evil",
                "summary": "must reject symlink",
                "sourceAnnotations": ["AN-EVIL"],
                "changes": [
                    {
                        "file": "chapters/evil.md",
                        "targetBlockId": "evil-p001",
                        "operation": "replace_block",
                        "beforeHash": "sha256:evil",
                        "afterText": "escaped write",
                        "reason": "test symlink rejection",
                    }
                ],
            }
            runtime = RuntimeOrchestrator(project)

            validation = runtime.validate_patch(patch)
            accepted = runtime.accept_patch(patch)

            self.assertFalse(validation["valid"])
            self.assertTrue(any(issue["code"] == "forbidden_file" for issue in validation["issues"]))
            self.assertFalse(accepted["applied"])
            self.assertEqual("<!-- mw:block id=evil-p001 hash=sha256:evil -->\noutside\n", outside.read_text(encoding="utf-8"))

    def test_long_lived_runtime_reloads_context_between_transactions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.copy_sample(Path(tmp))
            runtime = RuntimeOrchestrator(project)
            insert_patch = {
                "id": "PP-insert-first",
                "summary": "insert before replacing later block",
                "sourceAnnotations": ["USER-insert"],
                "changes": [
                    {
                        "file": "chapters/ch05.md",
                        "targetBlockId": "ch05-p017",
                        "operation": "insert_after_block",
                        "beforeHash": "sha256:8cc91a",
                        "afterText": "新插入的一段。",
                        "reason": "test stale context prevention",
                    }
                ],
            }
            stale_patch = make_annotation_patch(runtime.context, "AN-041")

            inserted = runtime.accept_patch(insert_patch)
            replaced = runtime.accept_patch(stale_patch)
            text = (project / "chapters" / "ch05.md").read_text(encoding="utf-8")

            self.assertTrue(inserted["applied"], inserted)
            self.assertTrue(replaced["applied"], replaced)
            self.assertEqual(text.count("<!-- mw:block id=ch05-p017 hash=sha256:8cc91a -->"), 1)
            self.assertEqual(text.count("mw:block id=ch05-p018"), 1)
            self.assertIn("新插入的一段。", text)
            self.assertIn("纸杯沿一点点捏扁", text)

    def test_insert_before_after_and_delete_operations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.copy_sample(Path(tmp))
            context = load_project(project)
            patch = {
                "id": "PP-ops",
                "summary": "exercise operations",
                "sourceAnnotations": ["USER-ops"],
                "changes": [
                    {
                        "file": "chapters/ch05.md",
                        "targetBlockId": "ch05-p017",
                        "operation": "insert_after_block",
                        "beforeHash": "sha256:8cc91a",
                        "afterText": "新插入的一段。",
                        "reason": "test insert after",
                    },
                    {
                        "file": "chapters/ch05.md",
                        "targetBlockId": "ch05-p019",
                        "operation": "delete_block",
                        "beforeHash": "sha256:6bd2aa",
                        "afterText": "",
                        "reason": "test delete",
                    },
                ],
            }

            result = apply_patch(context, patch)
            text = (project / "chapters" / "ch05.md").read_text(encoding="utf-8")

            self.assertTrue(result.valid, result.error_messages())
            self.assertIn("新插入的一段。", text)
            self.assertNotIn("你最后一次见到她是什么时候", text)
            self.assertIn("<!-- mw:block id=ch05-p017 hash=sha256:8cc91a -->", text)
            self.assertEqual(text.count("<!-- mw:block id=ch05-p017 hash=sha256:8cc91a -->"), 1)
            self.assertRegex(text, r"<!-- mw:block id=ch05-p017-ins-[0-9a-f]{8} hash=sha256:[0-9a-f]{6} -->")

    def test_skill_discovery_and_runtime_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.copy_sample(Path(tmp))
            skills = resolve_skills(discover_skills({"builtin": SKILLS}))
            runtime = RuntimeOrchestrator(project, builtin_skills_root=SKILLS)

            revise = runtime.run_skill("revise-with-annotations", annotation_ids=["AN-041"])
            rules = runtime.run_skill("extract-writing-rules", annotation_ids=["AN-041"])
            propagate = runtime.run_skill("propagate-rules")
            events = AuditLog(project).read()

            self.assertIn("revise-with-annotations", skills)
            self.assertEqual(revise["output"]["validation"]["valid"], True)
            self.assertEqual(rules["output"]["rules"][0]["source_annotations"], ["AN-041"])
            self.assertIn("chapters/ch05.md", propagate["output"]["patchProposalsByChapter"])
            self.assertIn("run.started", [event["type"] for event in events])


    def test_builtin_reserved_safety_skill_cannot_be_shadowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_skill = root / "project" / "safe-patch-apply"
            builtin_skill = root / "builtin" / "safe-patch-apply"
            project_skill.mkdir(parents=True)
            builtin_skill.mkdir(parents=True)
            project_skill.joinpath("SKILL.md").write_text(
                "---\nname: safe-patch-apply\ndescription: project override\n---\n",
                encoding="utf-8",
            )
            builtin_skill.joinpath("SKILL.md").write_text(
                "---\nname: safe-patch-apply\ndescription: trusted builtin\n---\n",
                encoding="utf-8",
            )

            skills = resolve_skills(discover_skills({"project": root / "project", "builtin": root / "builtin"}))

            self.assertEqual(skills["safe-patch-apply"].scope, "builtin")
            self.assertEqual(skills["safe-patch-apply"].description, "trusted builtin")

    def test_cli_smoke_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.copy_sample(Path(tmp))
            patch_path = Path(tmp) / "patch.json"
            generate = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "book_workbench.cli",
                    "generate-sample-patch",
                    "--project",
                    str(project),
                    "--annotation",
                    "AN-041",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            patch_path.write_text(generate.stdout, encoding="utf-8")

            validate = subprocess.run(
                [sys.executable, "-m", "book_workbench.cli", "validate", "--project", str(project), "--patch", str(patch_path)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            diff = subprocess.run(
                [sys.executable, "-m", "book_workbench.cli", "diff", "--project", str(project), "--patch", str(patch_path)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            run_skill = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "book_workbench.cli",
                    "run-skill",
                    "--project",
                    str(project),
                    "--skills-root",
                    str(SKILLS),
                    "--skill",
                    "revise-with-annotations",
                    "--annotation",
                    "AN-041",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(generate.returncode, 0, generate.stderr)
            self.assertEqual(validate.returncode, 0, validate.stderr)
            self.assertEqual(validate.stdout.strip(), "valid")
            self.assertEqual(diff.returncode, 0, diff.stderr)
            self.assertIn("纸杯沿一点点捏扁", diff.stdout)
            self.assertEqual(run_skill.returncode, 0, run_skill.stderr)
            self.assertEqual(json.loads(run_skill.stdout)["output"]["validation"]["valid"], True)

    def test_git_wrapper_clean_commit_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            ensure_repo(repo)
            (repo / "README.md").write_text("hello\n", encoding="utf-8")
            commit_all(repo, "Initial local fixture\n\nConfidence: high\nCo-authored-by: OmX <omx@oh-my-codex.dev>")
            before = status(repo)
            commit_all(repo, "No-op\n\nConfidence: high\nCo-authored-by: OmX <omx@oh-my-codex.dev>")
            after = status(repo)

            self.assertIn("##", before)
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
