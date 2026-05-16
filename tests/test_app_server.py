from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from book_workbench.app_server import create_server

SAMPLE = ROOT / "manuscript_runtime_codex_appserver_v2" / "sample_project"
SKILLS = ROOT / "manuscript_runtime_codex_appserver_v2" / "skills"


class FakeCodexClient:
    def health(self) -> dict:
        return {
            "ok": True,
            "command": ["fake-codex", "app-server"],
            "error": None,
            "response": {
                "userAgent": "book-workbench-test/0.1.0",
                "codexHome": "/tmp/fake-codex-home",
                "platformFamily": "unix",
                "platformOs": "test",
            },
            "stderr": "",
            "notifications": [],
            "durationMs": 1,
        }


class AppServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "sample_project"
        shutil.copytree(SAMPLE, self.project)
        self.server = create_server(
            self.project,
            builtin_skills_root=SKILLS,
            port=0,
            codex_client=FakeCodexClient(),  # deterministic unit tests
            local_token="test-token",
            quiet=True,
        )
        host, port = self.server.server_address[:2]
        self.base_url = f"http://{host}:{port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def get(self, path: str) -> object:
        with urllib.request.urlopen(self.base_url + path, timeout=5) as response:
            body = response.read()
            content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type:
            return json.loads(body.decode("utf-8"))
        return body.decode("utf-8")

    def post(self, path: str, payload: dict) -> dict:
        return self.post_with_headers(path, payload)

    def post_with_headers(self, path: str, payload: dict, headers: dict | None = None) -> dict:
        data = json.dumps(payload).encode("utf-8")
        request_headers = {
            "Content-Type": "application/json",
            "X-BookWorkbench-Token": "test-token",
        }
        if headers:
            request_headers.update(headers)
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method="POST",
            headers=request_headers,
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_index_and_health_endpoints(self) -> None:
        html = self.get("/")
        health = self.get("/api/health")

        self.assertIn("BookWorkbench Local App", html)
        self.assertIn('"test-token"', html)
        self.assertEqual(health["app"]["ok"], True)
        self.assertEqual(health["runtime"]["annotations"], 1)
        self.assertEqual(health["codex"]["ok"], True)

    def test_project_annotations_chapter_and_audit_endpoints(self) -> None:
        project = self.get("/api/project")
        annotations = self.get("/api/annotations?include_resolved=1")
        encoded = urllib.parse.quote("chapters/ch05.md", safe="")
        chapter = self.get(f"/api/chapters/{encoded}")
        audit = self.get("/api/audit")

        self.assertIn("chapters/ch05.md", project["blocks"])
        self.assertEqual(annotations["annotations"][0]["id"], "AN-041")
        self.assertEqual(chapter["status"], "draft")
        self.assertIn("ch05-p018", chapter["blocks"])
        self.assertEqual(audit["events"], [])

    def test_skill_preview_apply_and_audit_flow(self) -> None:
        run = self.post(
            "/api/skills/run",
            {"skill": "revise-with-annotations", "annotationIds": ["AN-041"]},
        )
        patch = run["output"]
        preview = self.post("/api/patch/preview", {"patch": patch})
        apply_result = self.post("/api/patch/apply", {"patch": patch})
        chapter_text = (self.project / "chapters" / "ch05.md").read_text(encoding="utf-8")
        audit = self.get("/api/audit")

        self.assertEqual(run["skill"], "revise-with-annotations")
        self.assertTrue(patch["validation"]["valid"], patch["validation"]["issues"])
        self.assertTrue(preview["validation"]["valid"], preview)
        self.assertIn("纸杯沿一点点捏扁", preview["diff"])
        self.assertTrue(apply_result["applied"], apply_result)
        self.assertIn("纸杯沿一点点捏扁", chapter_text)
        self.assertIn("patch.applied", [event["type"] for event in audit["events"]])

    def test_bad_chapter_path_returns_400(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/api/chapters/" + urllib.parse.quote("../rules.yaml", safe=""))

        self.assertEqual(caught.exception.code, 400)

    def test_post_requires_local_token(self) -> None:
        data = json.dumps({"skill": "revise-with-annotations"}).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/api/skills/run",
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)

        self.assertEqual(caught.exception.code, 403)

    def test_post_rejects_wrong_local_token(self) -> None:
        data = json.dumps({"skill": "revise-with-annotations"}).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/api/skills/run",
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-BookWorkbench-Token": "wrong-token",
            },
        )

        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)

        self.assertEqual(caught.exception.code, 403)

    def test_post_rejects_wrong_content_type(self) -> None:
        data = json.dumps({"skill": "revise-with-annotations"}).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/api/skills/run",
            data=data,
            method="POST",
            headers={
                "Content-Type": "text/plain",
                "X-BookWorkbench-Token": "test-token",
            },
        )

        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)

        self.assertEqual(caught.exception.code, 415)

    def test_post_rejects_cross_origin_request(self) -> None:
        data = json.dumps({"skill": "revise-with-annotations"}).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/api/skills/run",
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-BookWorkbench-Token": "test-token",
                "Origin": "http://evil.example",
            },
        )

        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)

        self.assertEqual(caught.exception.code, 403)

    def test_rejects_non_local_host_header(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post_with_headers(
                "/api/skills/run",
                {"skill": "revise-with-annotations"},
                headers={"Host": "evil.example"},
            )

        self.assertEqual(caught.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
