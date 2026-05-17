from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from book_workbench.app_server import RuntimeWebApp
from book_workbench.git_service import status
from book_workbench.patch_engine import make_annotation_patch, validate_patch
from book_workbench.project import index_markdown_blocks, load_project
from book_workbench.runtime import RuntimeOrchestrator
from tests.test_fixtures import write_black_rain_fixture


class ReleaseGateRuntimeTests(unittest.TestCase):
    def make_project(self, tmp: str, *, init_git: bool = False) -> Path:
        return write_black_rain_fixture(Path(tmp) / "black-rain-after", init_git=init_git)

    def test_tc001_markdown_selection_annotation_sidecar_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.make_project(tmp)
            app = RuntimeWebApp(project, workspace_root=Path(tmp))
            chapter = project / "chapters" / "ch05.md"
            before = chapter.read_text(encoding="utf-8")

            result = app.create_annotation(
                {
                    "file": "chapters/ch05.md",
                    "blockId": "ch05-p018",
                    "selectedText": "我的心里很复杂，我想起了过去的种种，内心充满了矛盾和挣扎。",
                    "text": "这里太像 AI，不要解释内心，用动作表现。",
                }
            )
            annotations = (project / ".bookai" / "annotations.jsonl").read_text(encoding="utf-8")
            block_index = json.loads((project / ".bookai" / "block-index.json").read_text(encoding="utf-8"))

            self.assertEqual(before, chapter.read_text(encoding="utf-8"))
            self.assertIn(result["annotation"]["id"], annotations)
            self.assertIn('"blockId": "ch05-p018"', annotations)
            self.assertIn('"beforeHash": "sha256:a91f3c"', annotations)
            self.assertIn('"startOffset"', annotations)
            self.assertIn('"endOffset"', annotations)
            self.assertEqual(block_index["chapters/ch05.md"]["ch05-p018"]["hash"], "sha256:a91f3c")

    def test_tc002_ai_revise_generates_patch_without_direct_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.make_project(tmp)
            runtime = RuntimeOrchestrator(project)
            chapter = project / "chapters" / "ch05.md"
            before = chapter.read_text(encoding="utf-8")

            run = runtime.run_skill("revise-with-annotations", annotation_ids=["AN-041"])
            patch = run["output"]

            self.assertEqual(before, chapter.read_text(encoding="utf-8"))
            self.assertEqual(patch["sourceAnnotations"], ["AN-041"])
            self.assertTrue(patch["validation"]["valid"], patch["validation"]["issues"])
            self.assertEqual({change["file"] for change in patch["changes"]}, {"chapters/ch05.md"})
            self.assertEqual({change["targetBlockId"] for change in patch["changes"]}, {"ch05-p018"})

    def test_tc003_locked_chapter_and_codex_file_change_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.make_project(tmp)
            context = load_project(project)
            patch = make_annotation_patch(context, "AN-041")
            patch["changes"][0].update({"file": "chapters/ch01.md", "targetBlockId": "ch01-p001", "beforeHash": "sha256:111111"})
            result = validate_patch(context, patch)
            approval = RuntimeOrchestrator(project).evaluate_file_change_request({"changes": [{"path": "chapters/ch01.md"}]})

            self.assertFalse(result.valid)
            self.assertTrue(any(issue.code == "locked_chapter" for issue in result.issues))
            self.assertEqual(approval["decision"], "decline")
            self.assertIn("locked_chapter:chapters/ch01.md", approval["reason"])

    def test_tc004_reviewed_chapter_requires_secondary_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.make_project(tmp)
            context = load_project(project)
            patch = {
                "id": "PP-reviewed",
                "summary": "reviewed edit",
                "sourceAnnotations": ["USER-review"],
                "changes": [
                    {
                        "file": "chapters/ch02.md",
                        "targetBlockId": "ch02-p001",
                        "operation": "replace_block",
                        "beforeHash": "sha256:222222",
                        "afterText": "二次确认后的文本。",
                        "reason": "explicit approval test",
                    }
                ],
            }
            rejected = validate_patch(context, patch)
            patch["changes"][0]["requiresSecondaryApproval"] = True
            accepted = validate_patch(context, patch, allow_reviewed=True)

            self.assertFalse(rejected.valid)
            self.assertTrue(any(issue.code == "reviewed_chapter_requires_secondary_approval" for issue in rejected.issues))
            self.assertTrue(accepted.valid, accepted.error_messages())

    def test_tc005_rule_propagation_only_draft_and_unreviewed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.make_project(tmp)
            annotations_path = project / ".bookai" / "annotations.jsonl"
            extra = [
                ("AN-301", "chapters/ch01.md", "ch01-p001", "sha256:111111"),
                ("AN-302", "chapters/ch02.md", "ch02-p001", "sha256:222222"),
                ("AN-303", "chapters/ch03.md", "ch03-p001", "sha256:333333"),
                ("AN-304", "chapters/ch04.md", "ch04-p001", "sha256:444444"),
            ]
            with annotations_path.open("a", encoding="utf-8") as handle:
                for aid, file_path, block_id, before_hash in extra:
                    handle.write("\n" + json.dumps({
                        "id": aid,
                        "file": file_path,
                        "target": {"blockId": block_id, "selectedText": "", "beforeHash": before_hash, "confidence": 0.99},
                        "body": {"text": "应用 R-018。", "type": "style", "priority": "high"},
                        "metadata": {"status": "open"},
                    }, ensure_ascii=False))
            output = RuntimeOrchestrator(project).run_skill("propagate-rules")["output"]

            self.assertEqual(set(output["patchProposalsByChapter"]), {"chapters/ch03.md", "chapters/ch04.md", "chapters/ch05.md"})
            excluded = {(item["file"], item["status"]) for item in output["excluded"]}
            self.assertIn(("chapters/ch01.md", "locked"), excluded)
            self.assertIn(("chapters/ch02.md", "reviewed"), excluded)
            for proposals in output["patchProposalsByChapter"].values():
                for patch in proposals:
                    self.assertIn("R-018", patch["rulesUsed"])

    def test_tc006_before_hash_mismatch_blocks_stale_annotation_patch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.make_project(tmp)
            chapter = project / "chapters" / "ch05.md"
            chapter.write_text(chapter.read_text(encoding="utf-8").replace("我的心里很复杂", "我的手指在桌沿停住"), encoding="utf-8")
            run = RuntimeOrchestrator(project).run_skill("revise-with-annotations", annotation_ids=["AN-041"])
            issues = run["output"]["validation"]["issues"]
            before = chapter.read_text(encoding="utf-8")
            accepted = RuntimeOrchestrator(project).accept_patch(run["output"])

            self.assertFalse(run["output"]["validation"]["valid"])
            self.assertTrue(any(issue["code"] == "hash_mismatch" for issue in issues))
            self.assertFalse(accepted["applied"])
            self.assertEqual(before, chapter.read_text(encoding="utf-8"))

    def test_tc007_malicious_annotation_is_untrusted_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.make_project(tmp)
            runtime = RuntimeOrchestrator(project)
            before_status = (project / ".bookai" / "chapter-status.yaml").read_text(encoding="utf-8")
            run = runtime.run_skill("revise-with-annotations", annotation_ids=["AN-999"])
            patch = run["output"]
            accepted = runtime.accept_patch(patch)

            self.assertTrue(patch["safety"]["promptInjectionSuspected"])
            self.assertFalse(patch["validation"]["valid"])
            self.assertFalse(accepted["applied"])
            self.assertEqual(before_status, (project / ".bookai" / "chapter-status.yaml").read_text(encoding="utf-8"))
            self.assertTrue((project / "chapters" / "ch01.md").exists())

    def test_tc008_malformed_patch_proposals_all_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.make_project(tmp)
            context = load_project(project)
            valid = make_annotation_patch(context, "AN-041")
            cases = []
            for mutation in (
                lambda p: p["changes"][0].pop("file"),
                lambda p: p["changes"][0].pop("beforeHash"),
                lambda p: p["changes"][0].update({"targetBlockId": "missing"}),
                lambda p: p["changes"][0].update({"operation": "delete_file"}),
                lambda p: p["changes"][0].update({"file": "../secrets.txt"}),
                lambda p: p["changes"][0].update({"file": "/tmp/evil.md"}),
                lambda p: p["changes"][0].update({"afterText": "<!-- mw:block id=x hash=y -->\nbad"}),
                lambda p: p.update({"rulesUsed": ["R-NOPE"]}),
                lambda p: p.update({"sourceAnnotations": ["AN-NOPE"]}),
            ):
                patch = json.loads(json.dumps(valid, ensure_ascii=False))
                mutation(patch)
                cases.append(patch)
            before = (project / "chapters" / "ch05.md").read_text(encoding="utf-8")
            for patch in cases:
                result = validate_patch(context, patch)
                self.assertFalse(result.valid, patch)
            self.assertEqual(before, (project / "chapters" / "ch05.md").read_text(encoding="utf-8"))

    def test_tc009_codex_appserver_file_change_approval_goes_through_runtime_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.make_project(tmp)
            runtime = RuntimeOrchestrator(project)

            dangerous = runtime.evaluate_file_change_request({"fileChanges": [{"path": ".bookai/chapter-status.yaml"}, {"path": "chapters/ch01.md"}]})
            direct_draft = runtime.evaluate_file_change_request({"fileChanges": [{"path": "chapters/ch05.md"}]})

            self.assertEqual(dangerous["decision"], "decline")
            self.assertIn("metadata_requires_runtime_tool:.bookai/chapter-status.yaml", dangerous["reason"])
            self.assertIn("locked_chapter:chapters/ch01.md", dangerous["reason"])
            self.assertEqual(direct_draft["decision"], "decline")
            self.assertIn("direct_manuscript_write_requires_patch_proposal:chapters/ch05.md", direct_draft["reason"])

    def test_tc010_accept_patch_creates_git_commit_reject_keeps_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.make_project(tmp, init_git=True)
            runtime = RuntimeOrchestrator(project)
            patch = runtime.run_skill("revise-with-annotations", annotation_ids=["AN-041"])["output"]
            before_count = int(subprocess.check_output(["git", "rev-list", "--count", "HEAD"], cwd=project, text=True).strip())
            accepted = runtime.accept_patch(patch)
            after_count = int(subprocess.check_output(["git", "rev-list", "--count", "HEAD"], cwd=project, text=True).strip())
            dirty_after_accept = subprocess.check_output(["git", "status", "--short"], cwd=project, text=True).strip()
            blocks = index_markdown_blocks(project, "chapters/ch05.md")
            block_index = json.loads((project / ".bookai" / "block-index.json").read_text(encoding="utf-8"))
            bad = json.loads(json.dumps(patch, ensure_ascii=False))
            bad["changes"][0]["beforeHash"] = "sha256:stale"
            rejected = runtime.accept_patch(bad)

            self.assertTrue(accepted["applied"], accepted)
            self.assertIsNone(accepted["commitError"])
            self.assertEqual(after_count, before_count + 1)
            self.assertEqual(dirty_after_accept, "")
            self.assertEqual(block_index["chapters/ch05.md"]["ch05-p018"]["hash"], blocks["ch05-p018"].before_hash)
            self.assertFalse(rejected["applied"])
            self.assertNotIn("纸杯沿一点点捏扁", status(project))

    def test_tc011_concurrent_runs_same_block_second_stale_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.make_project(tmp, init_git=True)
            runtime = RuntimeOrchestrator(project)
            run_a = runtime.run_skill("revise-with-annotations", annotation_ids=["AN-041"])["output"]
            run_b = RuntimeOrchestrator(project).run_skill("revise-with-annotations", annotation_ids=["AN-041"])["output"]

            first = runtime.accept_patch(run_a)
            second = runtime.accept_patch(run_b)

            self.assertTrue(first["applied"], first)
            self.assertFalse(second["applied"], second)
            self.assertTrue(any(issue["code"] == "hash_mismatch" for issue in second["validation"]["issues"]))


if __name__ == "__main__":
    unittest.main()
