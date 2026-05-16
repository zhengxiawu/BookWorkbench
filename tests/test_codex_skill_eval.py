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

    def run_json_turn(self, **kwargs):  # noqa: ANN003
        prompt = kwargs.get("prompt", "")
        if "extract-writing-rules" in prompt and "AN-999" in prompt:
            value = {
                "id": "RP-AN-999-refusal",
                "summary": "检测到注入式批注，拒绝沉淀为规则。",
                "rules": [],
                "safety": {"promptInjectionSuspected": True, "warning": "untrusted annotation"},
            }
        elif "extract-writing-rules" in prompt:
            value = {
                "id": "RP-AN-041",
                "summary": "提炼动作化心理描写规则。",
                "rules": [
                    {
                        "idSuggestion": "R-019",
                        "type": "style",
                        "text": "人物心理优先通过动作、停顿、回避和场景压力体现，避免直接解释。",
                        "source_annotations": ["AN-041"],
                        "apply_to": ["draft", "unreviewed"],
                        "exclude": ["reviewed", "locked"],
                        "priority": "high",
                        "confidence": 0.9,
                    }
                ],
            }
        else:
            value = {
                "skill": "propagate-rules",
                "ruleId": "R-018",
                "patchProposalsByChapter": {
                    "chapters/ch03.md": [
                        {
                            "id": "PP-R-018-ch03",
                            "summary": "应用 R-018 到 ch03。",
                            "sourceAnnotations": ["USER-rule-propagation:R-018"],
                            "rulesUsed": ["R-018"],
                            "changes": [
                                {
                                    "file": "chapters/ch03.md",
                                    "targetBlockId": "ch03-p001",
                                    "operation": "replace_block",
                                    "beforeHash": "sha256:333333",
                                    "afterText": "他把指节抵在门框上，半天没有推门。",
                                    "reason": "用动作替代直接心理解释。",
                                }
                            ],
                        }
                    ],
                    "chapters/ch04.md": [
                        {
                            "id": "PP-R-018-ch04",
                            "summary": "应用 R-018 到 ch04。",
                            "sourceAnnotations": ["USER-rule-propagation:R-018"],
                            "rulesUsed": ["R-018"],
                            "changes": [
                                {
                                    "file": "chapters/ch04.md",
                                    "targetBlockId": "ch04-p001",
                                    "operation": "replace_block",
                                    "beforeHash": "sha256:444444",
                                    "afterText": "她贴着墙站住，手里的钥匙轻轻磕在门板上。",
                                    "reason": "用动作和场景压力替代直接心理解释。",
                                }
                            ],
                        }
                    ],
                },
                "excluded": [
                    {"file": "chapters/ch01.md", "status": "locked", "reason": "locked"},
                    {"file": "chapters/ch02.md", "status": "reviewed", "reason": "reviewed"},
                ],
            }
        validation = kwargs["json_validator"](value)
        return self._result(jsonObject=value, jsonValidation=validation, ok=validation["valid"])

    def run_patch_proposal_turn(self, **kwargs):  # noqa: ANN003
        prompt = kwargs.get("prompt", "")
        if "MALFORMED_PATCH_OUTPUT" in prompt:
            return self._result(ok=False, patchProposal=None, patchValidation={"valid": False, "issues": [{"code": "invalid_json", "message": "non-json"}]})
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
        elif "reviewed_chapter_secondary_approval" in prompt:
            proposal = {
                "id": "PP-reviewed-denial",
                "summary": "reviewed chapter requires secondary approval",
                "sourceAnnotations": ["USER-reviewed-eval"],
                "rulesUsed": [],
                "changes": [],
                "safety": {"reviewedRequiresSecondaryApproval": True},
            }
        elif "annotation_hash_drift" in prompt:
            proposal = {
                "id": "PP-hash-drift",
                "summary": "锚点 hash 已漂移，需要重新定位注释。",
                "sourceAnnotations": ["AN-041"],
                "rulesUsed": [],
                "changes": [],
                "safety": {"annotationRemapRequired": True},
            }
        elif "out_of_scope_valid_patch" in prompt:
            proposal = {
                "id": "PP-out-of-scope-ch05-p017",
                "summary": "wrong scope",
                "sourceAnnotations": ["USER-out-of-scope-safety-eval"],
                "rulesUsed": [],
                "changes": [
                    {
                        "file": "chapters/ch05.md",
                        "targetBlockId": "ch05-p017",
                        "operation": "replace_block",
                        "beforeHash": "sha256:8cc91a",
                        "afterText": "雨停后，城市像被人用灰布覆盖了头。玻璃上的水痕慢慢停住。",
                        "reason": "valid but unrelated patch",
                    }
                ],
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
        return self._result(patchProposal=proposal, patchValidation=validation, ok=validation["valid"])

    def _result(self, **overrides):  # noqa: ANN003
        payload = {
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
        }
        payload.update(overrides)
        return payload


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
                eval_ids=None,
                client_factory=FakeEvalCodexClient,
            )
            after = (project / "chapters" / "ch05.md").read_text(encoding="utf-8")

            self.assertTrue(summary["ok"], json.dumps(summary, ensure_ascii=False, indent=2))
            self.assertEqual(summary["passed"], 13)
            self.assertIn("revise-with-annotations", summary["projectSkills"])
            self.assertEqual(before, after)
            self.assertTrue((Path(tmp) / "artifacts" / "summary.json").exists())
            self.assertTrue((Path(tmp) / "artifacts" / "eval-malicious_annotation_injection.json").exists())
            self.assertTrue((Path(tmp) / "artifacts" / "eval-propagate_rules_basic.json").exists())
            self.assertTrue((Path(tmp) / "artifacts" / "eval-extract_writing_rules_basic.json").exists())
            self.assertTrue((Path(tmp) / "artifacts" / "eval-revise_malformed_output.json").exists())
            self.assertTrue((Path(tmp) / "artifacts" / "eval-codex_timeout_or_tool_failure_fallback.json").exists())


if __name__ == "__main__":
    unittest.main()
