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


if __name__ == "__main__":
    unittest.main()
