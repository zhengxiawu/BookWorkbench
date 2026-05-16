from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from book_workbench.patch_engine import apply_patch, make_annotation_patch, preview_diff, validate_patch
from book_workbench.project import load_project

SAMPLE = ROOT / "manuscript_runtime_codex_appserver_v2" / "sample_project"


class RuntimeTests(unittest.TestCase):
    def copy_sample(self, tmp_path: Path) -> Path:
        target = tmp_path / "sample_project"
        shutil.copytree(SAMPLE, target)
        return target

    def test_load_project_indexes_annotations_rules_and_blocks(self) -> None:
        context = load_project(SAMPLE)

        self.assertEqual(context.chapter_status["chapters/ch05.md"], "draft")
        self.assertEqual([rule.id for rule in context.rules], ["R-018"])
        self.assertEqual([annotation.id for annotation in context.annotations], ["AN-041"])
        block = context.block("chapters/ch05.md", "ch05-p018")
        self.assertEqual(block.before_hash, "sha256:a91f3c")
        self.assertIn("眼神里没有任何波动", block.text)

    def test_sample_patch_validates_and_previews_diff(self) -> None:
        context = load_project(SAMPLE)
        patch = make_annotation_patch(context, "AN-041")
        result = validate_patch(context, patch)

        self.assertTrue(result.valid, result.error_messages())
        self.assertEqual(patch["sourceAnnotations"], ["AN-041"])
        self.assertEqual(patch["rulesUsed"], ["R-018"])

        diff = preview_diff(context, patch)
        self.assertIn("-我坐在审讯室里，盯着对面的男人。他沉默，眼神里没有任何波动。", diff)
        self.assertIn("+我坐在审讯室里，盯着对面的男人。他没有看我，只把纸杯沿一点点捏扁。", diff)
        self.assertIn("<!-- mw:block id=ch05-p018 hash=sha256:a91f3c -->", diff)

    def test_apply_patch_preserves_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self.copy_sample(Path(tmp))
            context = load_project(project)
            patch = make_annotation_patch(context, "AN-041")

            result = apply_patch(context, patch)

            self.assertTrue(result.valid, result.error_messages())
            text = (project / "chapters" / "ch05.md").read_text(encoding="utf-8")
            self.assertIn("<!-- mw:block id=ch05-p018 hash=sha256:a91f3c -->", text)
            self.assertIn("纸杯沿一点点捏扁", text)
            self.assertNotIn("眼神里没有任何波动", text)

    def test_locked_chapter_is_rejected(self) -> None:
        context = load_project(SAMPLE)
        patch = make_annotation_patch(context, "AN-041")
        patch["changes"][0]["file"] = "chapters/ch01.md"

        result = validate_patch(context, patch)

        self.assertFalse(result.valid)
        self.assertTrue(any(issue.code in {"locked_chapter", "unknown_file"} for issue in result.issues))

    def test_missing_source_annotation_is_rejected(self) -> None:
        context = load_project(SAMPLE)
        patch = make_annotation_patch(context, "AN-041")
        patch["sourceAnnotations"] = []

        result = validate_patch(context, patch)

        self.assertFalse(result.valid)
        self.assertTrue(any(issue.code == "missing_sources" for issue in result.issues))

    def test_hash_mismatch_is_rejected(self) -> None:
        context = load_project(SAMPLE)
        patch = make_annotation_patch(context, "AN-041")
        patch["changes"][0]["beforeHash"] = "sha256:bad"

        result = validate_patch(context, patch)

        self.assertFalse(result.valid)
        self.assertTrue(any(issue.code == "hash_mismatch" for issue in result.issues))


if __name__ == "__main__":
    unittest.main()
