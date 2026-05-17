from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from book_workbench.patch_engine import validate_patch
from book_workbench.powerbook_importer import import_powerbook_project
from book_workbench.powerbook_workflow import markdown_chapter_to_patch
from book_workbench.project import load_project
from book_workbench.runtime import RuntimeOrchestrator


def write_minimal_powerbook(root: Path) -> Path:
    (root / "book" / "chapters").mkdir(parents=True)
    (root / "theory").mkdir()
    (root / "claims").mkdir()
    (root / "reviews" / "resolved").mkdir(parents=True)
    (root / "reviews" / "inbox").mkdir(parents=True)
    (root / "outputs").mkdir()
    (root / "templates").mkdir()
    (root / "scripts").mkdir()
    (root / "AGENTS.md").write_text(
        "# AGENTS.md\n\n默认采用完整章节 + 作者批注驱动修订。抽象术语第一次出现时，先用白话解释。\n",
        encoding="utf-8",
    )
    (root / "WORKFLOW.md").write_text(
        "# 完整章节 + 作者批注驱动修订\n\n"
        "## 1.1 书稿写法\n\n具体事情 -> 问题浮现 -> 概念抽象 -> 机制拆解。\n\n"
        "## 1.2 术语翻译规则\n\n先用白话解释，再给术语。\n",
        encoding="utf-8",
    )
    (root / "theory" / "core_definitions.md").write_text(
        "# 核心定义体系\n\n## 1. 全书核心命题\n\n权力，是稳定改写他人行动空间的能力。\n",
        encoding="utf-8",
    )
    (root / "claims" / "claim_register.yaml").write_text("version: \"0.1\"\nclaims: []\n", encoding="utf-8")
    (root / "book" / "outline.md").write_text(
        "---\nbook_title: \"权力测试书\"\n---\n\n# 全书目录\n\n### ch01 权力是什么\n",
        encoding="utf-8",
    )
    (root / "book" / "chapters" / "ch01_power.md").write_text(
        "---\n"
        "chapter: 1\n"
        "title: \"权力是什么\"\n"
        "version: \"0.1\"\n"
        "review_status: \"annotated\"\n"
        "review_round: 1\n"
        "---\n\n"
        "# 第一章 权力是什么\n\n"
        "第一段正文，包含一点抽象术语。\n\n"
        "> [!AUTHOR-NOTE]\n"
        "> id: ch01-n001\n"
        "> type: 风格\n"
        "> priority: P0\n"
        "> target: previous-paragraph\n"
        "> status: open\n"
        ">\n"
        "> 这里太硬，先用白话解释。\n\n"
        "第二段正文，继续论证。\n",
        encoding="utf-8",
    )
    (root / "book" / "chapters" / "ch02_body.md").write_text(
        "---\n"
        "chapter: 2\n"
        "title: \"身体与恐惧\"\n"
        "version: \"0.1\"\n"
        "review_status: \"revised\"\n"
        "review_round: 1\n"
        "---\n\n"
        "# 第二章 身体与恐惧\n\n"
        "他很紧张，心里充满了矛盾。\n",
        encoding="utf-8",
    )
    (root / "reviews" / "resolved" / "ch01_revision_log.md").write_text(
        "# ch01 Revision Log\n\n## 1. 本轮修订摘要\n\n处理作者批注。\n",
        encoding="utf-8",
    )
    (root / "outputs" / "reading_queue.md").write_text("# Reading Queue\n\n先读 ch01。\n", encoding="utf-8")
    (root / "templates" / "author_note.md").write_text("# template\n", encoding="utf-8")
    (root / "scripts" / "polish_chapters_gemini.py").write_text("print('noop')\n", encoding="utf-8")
    return root


def tree_hash(root: Path) -> str:
    payload = ""
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        payload += f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(root).as_posix()}\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def git_count(root: Path) -> int:
    return int(subprocess.check_output(["git", "rev-list", "--count", "HEAD"], cwd=root, text=True).strip())


def git_status_short(root: Path) -> str:
    return subprocess.check_output(["git", "status", "--short"], cwd=root, text=True).strip()


def git_last_commit_files(root: Path) -> set[str]:
    output = subprocess.check_output(["git", "show", "--name-only", "--format=", "HEAD"], cwd=root, text=True)
    return {line.strip() for line in output.splitlines() if line.strip()}


class PowerBookImporterTests(unittest.TestCase):
    def test_import_powerbook_creates_runtime_project_without_mutating_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = write_minimal_powerbook(Path(tmp) / "PowerBook")
            before_hash = tree_hash(source)
            result = import_powerbook_project(source, Path(tmp) / "workspace", slug="powerbook-test")

            self.assertEqual(tree_hash(source), before_hash)
            project = Path(result["root"])
            context = load_project(project)
            self.assertEqual(result["chapterCount"], 2)
            self.assertEqual(result["annotationCount"], 1)
            self.assertIn("chapters/ch01_power.md", context.blocks)
            self.assertEqual(context.chapter_status["chapters/ch01_power.md"], "annotated")
            self.assertEqual(context.status_for_file("chapters/ch01_power.md"), "unreviewed")
            self.assertEqual(context.chapter_status["chapters/ch02_body.md"], "revised")
            self.assertEqual(context.status_for_file("chapters/ch02_body.md"), "unreviewed")
            imported = json.loads((project / ".bookai" / "powerbook-import.json").read_text(encoding="utf-8"))
            self.assertEqual(result["baselineCommitCreated"], True)
            self.assertEqual(git_count(project), 1)
            self.assertEqual(git_status_short(project), "")
            second = next(item for item in imported["chapters"] if item["target"] == "chapters/ch02_body.md")
            self.assertEqual(second["reviewStatus"], "revised")
            self.assertEqual(second["bookWorkbenchStatus"], "unreviewed")
            self.assertEqual(context.annotations[0].id, "AN-CH01-001")
            self.assertIn("先用白话解释", context.annotations[0].text)
            self.assertNotIn("AUTHOR-NOTE", (project / "chapters" / "ch01_power.md").read_text(encoding="utf-8"))
            self.assertTrue((project / ".bookai" / "block-index.json").exists())
            self.assertTrue((project / "claims" / "claim_register.yaml").exists())
            self.assertTrue((project / "reviews" / "resolved" / "ch01_revision_log.md").exists())
            self.assertTrue((project / ".codex" / "skills" / "revise-with-annotations" / "SKILL.md").exists())

    def test_imported_project_can_preview_and_apply_patch_with_git_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = write_minimal_powerbook(Path(tmp) / "PowerBook")
            project = Path(import_powerbook_project(source, Path(tmp) / "workspace", slug="powerbook-test")["root"])
            self.assertEqual(git_count(project), 1)
            runtime = RuntimeOrchestrator(project)
            patch = runtime.run_skill("revise-with-annotations", annotation_ids=["AN-CH01-001"])["output"]
            preview = runtime.preview_patch(patch)
            accepted = runtime.accept_patch(patch)
            dirty = git_status_short(project)
            changed_files = git_last_commit_files(project)

            self.assertTrue(preview["validation"]["valid"], preview)
            self.assertIn("指节抵住桌沿", preview["diff"])
            self.assertTrue(accepted["applied"], accepted)
            self.assertEqual(git_count(project), 2)
            self.assertEqual(dirty, "")
            self.assertIn("chapters/ch01_power.md", changed_files)
            self.assertIn(".bookai/annotations.jsonl", changed_files)
            self.assertIn(".bookai/block-index.json", changed_files)
            self.assertNotIn("book.spec.md", changed_files)
            self.assertNotIn("scripts/polish_chapters_gemini.py", changed_files)
            self.assertEqual(load_project(project).annotations[0].status, "resolved")


    def test_powerbook_gemini_markdown_is_converted_to_patch_without_writing_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = write_minimal_powerbook(Path(tmp) / "PowerBook")
            before_hash = tree_hash(source)
            project = Path(import_powerbook_project(source, Path(tmp) / "workspace", slug="powerbook-test")["root"])
            context = load_project(project)
            markdown = """---
chapter: 1
title: "被改写的选择"
version: "0.2"
review_status: "revised"
---

# 第一章 被改写的选择

第一段正文，先把抽象术语放回一个普通人能看见的处境里。

第二段正文，继续论证。

第三段新增收束，说明权力怎样稳定改写行动空间。
"""

            patch = markdown_chapter_to_patch(context, "chapters/ch01_power.md", markdown)
            validation = validate_patch(context, patch)

            self.assertTrue(validation.valid, validation.issues)
            self.assertEqual(tree_hash(source), before_hash)
            self.assertEqual(patch["sourceAnnotations"], ["USER-powerbook-gemini-workflow"])
            self.assertEqual(patch["changes"][0]["file"], "chapters/ch01_power.md")
            self.assertEqual(patch["changes"][-1]["operation"], "insert_after_block")
            self.assertNotIn("mw:block", patch["changes"][0]["afterText"])

    def test_local_powerbook_workflow_fallback_is_diagnostic_only_and_not_applicable(self) -> None:
        from book_workbench.powerbook_workflow import build_powerbook_local_chapter_patch

        with tempfile.TemporaryDirectory() as tmp:
            source = write_minimal_powerbook(Path(tmp) / "PowerBook")
            project = Path(import_powerbook_project(source, Path(tmp) / "workspace", slug="powerbook-test")["root"])
            context = load_project(project)

            patch = build_powerbook_local_chapter_patch(context, "chapters/ch01_power.md", reason="timeout")
            result = validate_patch(context, patch)

            self.assertFalse(result.valid, result.issues)
            self.assertTrue(any(issue.code == "empty_changes" for issue in result.issues), result.issues)
            self.assertEqual(patch["workflow"]["source"], "local-workflow-fallback")
            self.assertTrue(patch["workflow"]["localFallback"])
            self.assertTrue(patch["workflow"]["diagnosticOnly"])
            self.assertTrue(patch["safety"]["acceptDisabled"])
            self.assertEqual(patch["changes"], [])
            self.assertIn("未生成可应用正文修改", patch["summary"])
            self.assertNotIn("这一段需要先落到可见处境", json.dumps(patch, ensure_ascii=False))

    def test_imported_source_annotation_blocks_stale_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = write_minimal_powerbook(Path(tmp) / "PowerBook")
            project = Path(import_powerbook_project(source, Path(tmp) / "workspace", slug="powerbook-test")["root"])
            context = load_project(project)
            annotation = context.annotations[0]
            patch = {
                "id": "PP-stale",
                "summary": "stale selected text",
                "sourceAnnotations": [annotation.id],
                "rulesUsed": [],
                "changes": [
                    {
                        "file": annotation.file,
                        "targetBlockId": annotation.block_id,
                        "operation": "replace_block",
                        "beforeHash": "sha256:bad",
                        "afterText": "替换文本。",
                        "reason": "test stale hash",
                    }
                ],
            }

            result = validate_patch(context, patch)

            self.assertFalse(result.valid)
            self.assertTrue(any(issue.code == "hash_mismatch" for issue in result.issues))


if __name__ == "__main__":
    unittest.main()
