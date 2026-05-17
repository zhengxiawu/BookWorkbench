from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from book_workbench.project_creator import PROJECT_SKILL_FILES, create_book_project
from book_workbench.runtime import RuntimeOrchestrator
from book_workbench.skill_manager import build_skill_roots, discover_skills, resolve_skills


class ProjectCreatorTests(unittest.TestCase):
    def test_created_project_scaffolds_project_local_codex_skills_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            global_skill_root = Path(tmp) / "global-home" / ".codex" / "skills"
            with mock.patch("pathlib.Path.home", return_value=Path(tmp) / "global-home"):
                result = create_book_project(
                    Path(tmp) / "workspace",
                    title="局部 Skill 测试",
                    slug="skill-scope-book",
                    opening_text="",
                )

            project = Path(result["root"])
            for relative in PROJECT_SKILL_FILES:
                skill_path = project / relative
                self.assertTrue(skill_path.exists(), relative)
                content = skill_path.read_text(encoding="utf-8")
                self.assertIn("project-local", content)
                self.assertIn("global Codex", content)
                self.assertIn("never", content.lower())

            self.assertFalse(global_skill_root.exists(), "project creation must not write ~/.codex/skills")
            self.assertIn("requiresSecondaryApproval", (project / ".codex/skills/revise-with-annotations/SKILL.md").read_text(encoding="utf-8"))
            self.assertIn("annotationRemapRequired", (project / ".codex/skills/revise-with-annotations/SKILL.md").read_text(encoding="utf-8"))
            self.assertIn("patchProposalsByChapter", (project / ".codex/skills/propagate-rules/SKILL.md").read_text(encoding="utf-8"))
            self.assertIn("rules: []", (project / ".codex/skills/extract-writing-rules/SKILL.md").read_text(encoding="utf-8"))
            self.assertTrue(all(path.startswith(".codex/skills/") for path in PROJECT_SKILL_FILES))
            self.assertTrue(all(path in result["createdFiles"] for path in PROJECT_SKILL_FILES))

    def test_runtime_discovers_generated_project_codex_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = create_book_project(
                Path(tmp) / "workspace",
                title="运行时 Skill 测试",
                slug="runtime-skill-book",
                opening_text="雨落在窗沿。",
            )
            project = Path(result["root"])

            roots = build_skill_roots(project_root=project)
            skills = resolve_skills(discover_skills(roots))
            runtime = RuntimeOrchestrator(project)

            self.assertEqual(roots["project"], project / ".codex" / "skills")
            self.assertEqual(skills["revise-with-annotations"].scope, "project")
            self.assertIn("revise-with-annotations", runtime.skills)
            self.assertEqual(runtime.skills["revise-with-annotations"].path, project / ".codex" / "skills" / "revise-with-annotations" / "SKILL.md")

    def test_powerbook_guide_mode_creates_full_chapter_and_local_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = create_book_project(
                Path(tmp) / "workspace",
                title="权力测试书",
                slug="powerbook-guide",
                mode="powerbook-guide",
                premise="我希望按 PowerBook / Codex 写书闭环，从权力是什么开始生成完整理论章节。",
                opening_text="Gemini 3.1 Pro、claim register、AUTHOR-NOTE、逐章内嵌批注闭环。",
                create_baseline_commit=True,
            )
            project = Path(result["root"])
            chapter = (project / "chapters" / "ch01.md").read_text(encoding="utf-8")

            self.assertEqual(result["plan"]["mode"], "powerbook-guide")
            self.assertTrue(result["baselineCommitCreated"], result)
            self.assertTrue((project / "AGENTS.md").exists())
            self.assertTrue((project / "WORKFLOW.md").exists())
            self.assertTrue((project / "theory" / "core_definitions.md").exists())
            self.assertTrue((project / "claims" / "claim_register.yaml").exists())
            self.assertTrue((project / ".bookai" / "powerbook-guide.json").exists())
            self.assertGreater(chapter.count("mw:block"), 7)
            self.assertGreater(len(chapter), 1500)
            self.assertIn("权力，是稳定改写他人行动空间的能力", chapter)



if __name__ == "__main__":
    unittest.main()
