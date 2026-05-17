from __future__ import annotations

import json
import shutil
import subprocess
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
from tests.test_fixtures import write_black_rain_fixture

SAMPLE = ROOT / "manuscript_runtime_codex_appserver_v2" / "sample_project"
SKILLS = ROOT / "manuscript_runtime_codex_appserver_v2" / "skills"


class FakeCodexClient:
    def __init__(self) -> None:
        self.skills_cwds = []
        self.probe_calls = []
        self.patch_probe_calls = []
        self.patch_mode = "valid"

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

    def list_skills(self, *, cwds, force_reload=True):  # noqa: ANN001
        self.skills_cwds.append([Path(cwd) for cwd in cwds])
        return {
            "ok": True,
            "response": {
                "data": [
                    {
                        "cwd": Path(cwds[0]).as_posix(),
                        "errors": [],
                        "skills": [
                            {
                                "name": "revise-with-annotations",
                                "scope": "repo",
                                "path": (Path(cwds[0]) / ".codex" / "skills" / "revise-with-annotations" / "SKILL.md").as_posix(),
                            }
                        ],
                    }
                ]
            },
        }

    def run_probe_turn(self, **kwargs):  # noqa: ANN003
        self.probe_calls.append(kwargs)
        approval_handler = kwargs.get("approval_handler")
        approval = approval_handler({"method": "item/fileChange/requestApproval", "params": {"fileChanges": [{"path": "chapters/ch01.md"}]}})
        return {"ok": True, "finalText": '{"ok": true}', "approvals": [{"response": approval}]}

    def run_patch_proposal_turn(self, **kwargs):  # noqa: ANN003
        self.patch_probe_calls.append(kwargs)
        if self.patch_mode == "invalid":
            proposal = {
                "id": "PP-invalid",
                "summary": "invalid probe",
                "sourceAnnotations": ["AN-041"],
                "rulesUsed": [],
                "changes": [
                    {
                        "file": "chapters/ch01.md",
                        "targetBlockId": "ch01-p001",
                        "operation": "replace_block",
                        "beforeHash": "sha256:111111",
                        "afterText": "should not apply",
                        "reason": "invalid locked change",
                    }
                ],
            }
        elif self.patch_mode == "user-book":
            proposal = {
                "id": "PP-codex-user-book",
                "summary": "codex main path for created project",
                "sourceAnnotations": ["AN-001"],
                "rulesUsed": ["R-001"],
                "changes": [
                    {
                        "file": "chapters/ch01.md",
                        "targetBlockId": "ch01-p001",
                        "operation": "replace_block",
                        "beforeHash": "sha256:3d7bdd",
                        "afterText": "我站在门口，把那封信翻到背面，指尖停在没有署名的空白处。",
                        "reason": "fake codex main path proposal",
                    }
                ],
            }
        elif self.patch_mode == "wrong-scope":
            proposal = {
                "id": "PP-wrong-scope",
                "summary": "valid but unrelated patch",
                "sourceAnnotations": ["USER-codex"],
                "rulesUsed": ["R-018"],
                "changes": [
                    {
                        "file": "chapters/ch05.md",
                        "targetBlockId": "ch05-p017",
                        "operation": "replace_block",
                        "beforeHash": "sha256:8cc91a",
                        "afterText": "雨停后，城市像被一只潮湿的手按住了喉咙。",
                        "reason": "wrong scope patch that runtime would otherwise accept",
                    }
                ],
            }
        else:
            proposal = {
                "id": "PP-probe",
                "summary": "probe",
                "sourceAnnotations": ["AN-041"],
                "rulesUsed": ["R-018"],
                "changes": [
                    {
                        "file": "chapters/ch05.md",
                        "targetBlockId": "ch05-p018",
                        "operation": "replace_block",
                        "beforeHash": "sha256:a91f3c",
                        "afterText": "我坐在审讯室里，盯着对面的男人。他没有看我，只把纸杯沿一点点捏扁。",
                        "reason": "probe patch proposal",
                    }
                ],
            }
        validation = kwargs["patch_validator"](proposal)
        return {
            "ok": validation["valid"],
            "threadId": "thread-test",
            "turnId": "turn-test",
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


class WorkspaceModeAppServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name) / "workspace"
        self.workspace.mkdir()
        self.server = create_server(
            None,
            workspace_root=self.workspace,
            builtin_skills_root=SKILLS,
            port=0,
            codex_client=FakeCodexClient(),
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
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json", "X-BookWorkbench-Token": "test-token"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_empty_workspace_index_has_no_demo_book_and_has_new_project_modal(self) -> None:
        html = self.get("/")
        health = self.get("/api/health")
        projects = self.get("/api/projects")

        self.assertIn("还没有书稿项目", html)
        self.assertIn('data-testid="new-project-modal"', html)
        self.assertNotIn("黑雨之后", html)
        self.assertNotIn("正在连接本地 Runtime", html)
        self.assertEqual(health["runtime"]["ok"], False)
        self.assertEqual(health["runtime"]["reason"], "no_project_open")
        self.assertEqual(health["codex"]["status"], "pending_project_open")
        self.assertNotIn("not_checked_until_project_open", html)
        self.assertEqual(projects["projects"], [])

    def test_create_project_lists_then_open_project(self) -> None:
        created = self.post(
            "/api/projects/create",
            {"title": "我的第一本书", "slug": "my-first-book", "openingText": ""},
        )
        projects = self.get("/api/projects")
        opened = self.post("/api/projects/open", {"relativePath": "my-first-book"})
        project = self.get("/api/project")
        chapter = self.get("/api/chapters/" + urllib.parse.quote("chapters/ch01.md", safe=""))
        discussions = self.get("/api/discussions")

        self.assertEqual(created["summary"]["title"], "我的第一本书")
        self.assertEqual([item["relativePath"] for item in projects["projects"]], ["my-first-book"])
        self.assertTrue(opened["project"]["open"] if "open" in opened["project"] else True)
        self.assertEqual(project["open"], True)
        self.assertIn("chapters/ch01.md", project["blocks"])
        self.assertEqual(chapter["blocks"]["ch01-p001"]["text"], "")
        self.assertTrue((Path(created["root"]) / ".bookai" / "discussions.jsonl").exists())
        self.assertTrue((Path(created["root"]) / ".codex" / "skills" / "revise-with-annotations" / "SKILL.md").exists())
        self.assertEqual(discussions["discussions"], [])


    def test_index_script_has_separate_existing_project_list_state(self) -> None:
        html = self.get("/")

        self.assertIn("const hasProjects = state.projects.length > 0", html)
        self.assertIn('data-testid="project-list-panel"', html)
        self.assertIn("请选择一个已有书稿项目，或新建自己的项目。", html)

    def test_create_discussion_writes_sidecar_and_not_chapter(self) -> None:
        created = self.post(
            "/api/projects/create",
            {
                "title": "讨论测试",
                "slug": "discussion-flow",
                "openingText": "我站在门口，心里很乱。",
            },
        )
        self.post("/api/projects/open", {"relativePath": "discussion-flow"})
        chapter_path = Path(created["root"]) / "chapters" / "ch01.md"
        before_chapter = chapter_path.read_text(encoding="utf-8")

        result = self.post(
            "/api/discussions/create",
            {
                "text": "讨论：这一段要从动作进入，不要直接解释心情。",
                "file": "chapters/ch01.md",
                "blockId": "ch01-p001",
            },
        )
        listed = self.get("/api/discussions")
        audit = self.get("/api/audit")

        self.assertEqual(result["discussion"]["id"], "DS-001")
        self.assertIn("不要直接解释心情", listed["discussions"][0]["text"])
        self.assertEqual(chapter_path.read_text(encoding="utf-8"), before_chapter)
        self.assertIn("discussion.created", [event["type"] for event in audit["events"]])

    def test_existing_fixture_can_be_opened_from_project_list(self) -> None:
        write_black_rain_fixture(self.workspace / "black-rain-after")
        projects = self.get("/api/projects")
        opened = self.post("/api/projects/open", {"relativePath": "black-rain-after"})
        annotations = self.get("/api/annotations?include_resolved=1")

        self.assertEqual(projects["projects"][0]["title"], "黑雨之后")
        self.assertIn("chapters/ch05.md", opened["project"]["blocks"])
        self.assertEqual({item["id"] for item in annotations["annotations"]}, {"AN-041", "AN-999"})


class AppServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "sample_project"
        shutil.copytree(SAMPLE, self.project)
        self.server = create_server(
            self.project,
            workspace_root=Path(self.tmp.name),
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
        self.assertIn("Manuscript Workbench", html)
        self.assertIn("Patch / Diff 审核", html)
        self.assertIn('"test-token"', html)
        self.assertEqual(health["app"]["ok"], True)
        self.assertEqual(health["runtime"]["annotations"], 1)
        self.assertEqual(health["codex"]["ok"], True)


    def test_index_script_is_syntactically_valid_and_contains_design_views(self) -> None:
        html = self.get("/")
        script_start = html.index("<script>") + len("<script>")
        script_end = html.index("</script>", script_start)
        script = html[script_start:script_end]

        self.assertIn("const BOOKWORKBENCH_TOKEN = \"test-token\";", script)
        self.assertIn('data-view="editor"', html)
        self.assertIn('id="view-diff"', html)
        self.assertIn('id="view-rules"', html)
        self.assertIn('id="partialPatchBtn" disabled', html)
        self.assertIn('部分应用（暂不可用）', html)
        self.assertIn('id="newRuleBtn" disabled', html)
        self.assertIn('新建规则（暂不可用）', html)
        self.assertIn('id="batchApplyBtn" disabled', html)
        self.assertIn('批量应用（暂不可用）', html)
        self.assertIn('id="previewRuleImpactBtn" disabled', html)
        self.assertIn('预览影响（暂不可用）', html)
        self.assertIn('id="ruleFilterBtn"', html)
        self.assertIn('data-rule-filter="style"', html)
        self.assertIn('id="toggleDiffReasonBtn"', html)
        self.assertIn('data-annotation-tab="suggestions"', html)
        self.assertIn('status-badge', html)
        self.assertIn('timeoutSeconds: 30', script)
        self.assertIn('function codexStatusLabel(codex)', script)
        self.assertIn('pending_project_open: "打开项目后检测"', script)
        self.assertIn('await loadHealth().catch(() => {})', script)
        self.assertIn('id="selectionMenu"', html)
        self.assertIn('id="selectionAddAnnotationBtn"', html)
        self.assertIn('function selectionContext()', script)
        self.assertIn('function openAnnotationFromSelection()', script)
        self.assertIn('addEventListener("contextmenu"', script)
        self.assertIn('addEventListener("dblclick"', script)
        self.assertIn('function selectedBlockIdFromSelection()', script)
        self.assertIn('openAnnotationModal())', script)
        self.assertNotIn('not_checked_until_project_open', html)
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
            handle.write(script)
            script_path = handle.name
        try:
            node = shutil.which("node")
            if node is None:
                self.skipTest("node is required for browser script syntax validation")
            completed = subprocess.run(
                [node, "--check", script_path],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        finally:
            Path(script_path).unlink(missing_ok=True)

    def test_codex_appserver_probe_endpoints_are_project_scoped_and_runtime_guarded(self) -> None:
        skills = self.post("/api/codex/skills", {})
        probe = self.post("/api/codex/probe", {"prompt": "probe", "timeoutSeconds": 1})
        patch_probe = self.post("/api/codex/patch-probe", {"timeoutSeconds": 1})
        fake = self.server.app.codex_client

        self.assertTrue(skills["ok"], skills)
        self.assertEqual(fake.skills_cwds[-1], [self.project.resolve()])
        skill = skills["response"]["data"][0]["skills"][0]
        self.assertEqual(skill["scope"], "repo")
        self.assertIn("/.codex/skills/", skill["path"])
        self.assertTrue(probe["ok"], probe)
        self.assertIn("locked_chapter:chapters/ch01.md", probe["approvals"][0]["response"]["reason"])
        self.assertEqual(fake.probe_calls[-1]["cwd"], self.project.resolve())
        self.assertTrue(patch_probe["ok"], patch_probe)
        self.assertEqual(patch_probe["patchValidation"], {"valid": True, "issues": []})
        self.assertEqual(fake.patch_probe_calls[-1]["cwd"], self.project.resolve())

    def test_ai_revise_uses_codex_patchproposal_when_runtime_valid(self) -> None:
        result = self.post(
            "/api/ai/revise",
            {"annotationIds": ["AN-041"], "file": "chapters/ch05.md"},
        )
        fake = self.server.app.codex_client

        self.assertEqual(result["source"], "codex-app-server")
        self.assertEqual(result["output"]["id"], "PP-probe")
        self.assertEqual(result["output"]["sourceAnnotations"], ["AN-041"])
        self.assertEqual(result["codex"]["patchValidation"], {"valid": True, "issues": []})
        self.assertEqual(fake.patch_probe_calls[-1]["cwd"], self.project.resolve())
        self.assertEqual(fake.patch_probe_calls[-1]["timeout_seconds"], 30.0)
        self.assertIn("revise-with-annotations", fake.patch_probe_calls[-1]["prompt"])
        self.assertIn("AN-041", fake.patch_probe_calls[-1]["prompt"])
        self.assertNotIn("纸杯沿一点点捏扁", (self.project / "chapters" / "ch05.md").read_text(encoding="utf-8"))

    def test_ai_revise_falls_back_when_codex_patchproposal_is_invalid(self) -> None:
        fake = self.server.app.codex_client
        fake.patch_mode = "invalid"
        result = self.post(
            "/api/ai/revise",
            {"annotationIds": ["AN-041"], "file": "chapters/ch05.md", "timeoutSeconds": 1},
        )

        self.assertEqual(result["source"], "runtime-deterministic")
        self.assertEqual(result["fallbackReason"], "codex_patch_failed_runtime_validation")
        self.assertEqual(result["output"]["sourceAnnotations"], ["AN-041"])
        self.assertTrue(result["output"]["validation"]["valid"], result["output"]["validation"])
        self.assertIn("locked_chapter", json.dumps(result["codex"]["patchValidation"], ensure_ascii=False))

    def test_ai_revise_falls_back_when_codex_patchproposal_is_valid_but_out_of_scope(self) -> None:
        fake = self.server.app.codex_client
        fake.patch_mode = "wrong-scope"
        result = self.post(
            "/api/ai/revise",
            {"annotationIds": ["AN-041"], "file": "chapters/ch05.md", "timeoutSeconds": 1},
        )

        self.assertEqual(result["source"], "runtime-deterministic")
        self.assertEqual(result["fallbackReason"], "codex_patch_out_of_annotation_scope")
        self.assertEqual(result["output"]["sourceAnnotations"], ["AN-041"])
        self.assertTrue(result["codex"]["patchValidation"]["valid"], result["codex"]["patchValidation"])
        self.assertNotIn("潮湿的手", (self.project / "chapters" / "ch05.md").read_text(encoding="utf-8"))

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

    def test_create_new_book_then_modify_first_chapter_flow(self) -> None:
        created = self.post(
            "/api/projects/create",
            {
                "title": "雾中来信",
                "slug": "new-book-flow",
                "openingText": "清晨六点，邮差把一封没有寄件人的信放在门缝里。",
            },
        )
        new_root = Path(created["root"])
        new_server = create_server(
            new_root,
            workspace_root=Path(self.tmp.name),
            port=0,
            codex_client=FakeCodexClient(),
            local_token="test-token",
            quiet=True,
        )
        new_thread = threading.Thread(target=new_server.serve_forever, daemon=True)
        new_thread.start()
        old_base = self.base_url
        try:
            host, port = new_server.server_address[:2]
            self.base_url = f"http://{host}:{port}"
            project = self.get("/api/project")
            chapter = self.get("/api/chapters/" + urllib.parse.quote("chapters/ch01.md", safe=""))
            block = chapter["blocks"]["ch01-p001"]
            patch = self.post(
                "/api/patch/manual",
                {
                    "file": "chapters/ch01.md",
                    "blockId": "ch01-p001",
                    "afterText": block["text"] + "\n门外的雾很低，像有人把城市的声音都压进了信封。",
                },
            )
            preview = self.post("/api/patch/preview", {"patch": patch})
            applied = self.post("/api/patch/apply", {"patch": patch})
            audit = self.get("/api/audit")
            chapter_text = (new_root / "chapters" / "ch01.md").read_text(encoding="utf-8")

            self.assertEqual(created["plan"]["slug"], "new-book-flow")
            self.assertTrue((new_root / "book.spec.md").exists())
            self.assertIn("chapters/ch01.md", project["blocks"])
            self.assertTrue(patch["validation"]["valid"], patch["validation"]["issues"])
            self.assertIn("压进了信封", preview["diff"])
            self.assertTrue(applied["applied"], applied)
            self.assertIn("压进了信封", chapter_text)
            self.assertIn("project.created", [event["type"] for event in audit["events"]])
            self.assertIn("patch.applied", [event["type"] for event in audit["events"]])
        finally:
            self.base_url = old_base
            new_server.shutdown()
            new_server.server_close()
            new_thread.join(timeout=2)

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
