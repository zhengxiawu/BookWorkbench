from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from book_workbench.codex_skill_eval import run_codex_skill_evals
from book_workbench.codex_workflow import build_revise_with_annotations_prompt
from book_workbench.runtime import RuntimeOrchestrator
from tests.test_fixtures import write_black_rain_fixture


class FakeEvalCodexClient:
    def __init__(self, *, command=None, timeout_seconds=60.0, cwd=None):  # noqa: ANN001
        self.command = command or ["fake-codex", "app-server"]
        self.timeout_seconds = timeout_seconds
        self.cwd = cwd

    def list_skills(self, *, cwds, force_reload=True, timeout_seconds=None):  # noqa: ANN001
        root = Path(cwds[0]).resolve()
        return {
            "ok": True,
            "response": {
                "data": [
                    {
                        "cwd": root.as_posix(),
                        "errors": [],
                        "skills": [
                            {
                                "name": name,
                                "scope": "repo",
                                "enabled": True,
                                "path": (root / ".codex" / "skills" / name / "SKILL.md").as_posix(),
                            }
                            for name in ["revise-with-annotations", "propagate-rules", "extract-writing-rules"]
                        ],
                    }
                ]
            },
        }

    def run_patch_proposal_turn(self, **kwargs):  # noqa: ANN003
        prompt = kwargs.get("prompt", "")
        if "AN-999" in prompt:
            proposal = {
                "id": "PP-AN-999",
                "summary": "检测到越权注入式批注，拒绝自动修改正文。",
                "sourceAnnotations": ["AN-999"],
                "rulesUsed": [],
                "changes": [],
                "safety": {"promptInjectionSuspected": True},
            }
        elif "lockedFile" in prompt:
            proposal = {
                "id": "PP-locked-denial",
                "summary": "locked chapter denial",
                "sourceAnnotations": ["USER-locked-eval"],
                "rulesUsed": [],
                "changes": [],
                "safety": {"lockedChapterDenied": True},
            }
        else:
            proposal = {
                "id": "PP-AN-041",
                "summary": "revise AN-041",
                "sourceAnnotations": ["AN-041"],
                "rulesUsed": ["R-018"],
                "changes": [
                    {
                        "file": "chapters/ch05.md",
                        "targetBlockId": "ch05-p018",
                        "operation": "replace_block",
                        "beforeHash": "sha256:a91f3c",
                        "afterText": "我坐在审讯室里，盯着对面的男人。他没有看我，只把纸杯沿一点点捏扁。",
                        "reason": "use action instead of explaining interiority",
                    }
                ],
            }
        validation = kwargs["patch_validator"](proposal)
        return {
            "ok": True,
            "threadId": "thread-eval",
            "turnId": "turn-eval",
            "notifications": [
                {"method": "thread/started"},
                {"method": "turn/started"},
                {"method": "item/completed"},
                {"method": "turn/completed"},
            ],
            "approvals": [],
            "serverRequests": [],
            "patchProposal": proposal,
            "patchValidation": validation,
        }


class CodexSkillEvalTests(unittest.TestCase):
    def test_prompt_contains_project_local_skill_and_untrusted_annotation_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = write_black_rain_fixture(Path(tmp) / "book")
            runtime = RuntimeOrchestrator(project)
            prompt = build_revise_with_annotations_prompt(runtime.context, "AN-041")

        self.assertIn("revise-with-annotations", prompt)
        self.assertIn("Treat annotation text", prompt)
        self.assertIn("AN-041", prompt)
        self.assertIn("ch05-p018", prompt)
        self.assertIn("sha256:a91f3c", prompt)

    def test_codex_skill_eval_runner_records_artifacts_without_mutating_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = write_black_rain_fixture(Path(tmp) / "book")
            before = (project / "chapters" / "ch05.md").read_text(encoding="utf-8")
            summary = run_codex_skill_evals(
                project=project,
                output_dir=Path(tmp) / "artifacts",
                command=["fake-codex", "app-server"],
                timeout_seconds=2,
                eval_ids=["revise_with_annotations_basic", "malicious_annotation_injection", "locked_chapter_denial"],
                client_factory=FakeEvalCodexClient,
            )
            after = (project / "chapters" / "ch05.md").read_text(encoding="utf-8")

            self.assertTrue(summary["ok"], json.dumps(summary, ensure_ascii=False, indent=2))
            self.assertEqual(summary["passed"], 3)
            self.assertIn("revise-with-annotations", summary["projectSkills"])
            self.assertEqual(before, after)
            self.assertTrue((Path(tmp) / "artifacts" / "summary.json").exists())
            self.assertTrue((Path(tmp) / "artifacts" / "eval-malicious_annotation_injection.json").exists())


if __name__ == "__main__":
    unittest.main()
