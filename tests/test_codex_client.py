from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from book_workbench.codex_client import CodexAppServerClient, _JsonRpcSession


class FakePipe(io.BytesIO):
    def close(self) -> None:
        # Keep BytesIO readable after CodexAppServerClient closes stdin during
        # process cleanup.
        pass

    def fileno(self) -> int:  # not used when _read_response is patched
        return 0


class FakeProcess:
    def __init__(self) -> None:
        self.stdin = FakePipe()
        self.stdout = FakePipe()
        self.stderr = FakePipe()
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):  # noqa: ANN001 - subprocess-compatible fake
        self.returncode = 0
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


class CodexClientTests(unittest.TestCase):
    def test_health_writes_initialize_request_and_returns_response(self) -> None:
        fake_process = FakeProcess()

        def fake_popen(*args, **kwargs):  # noqa: ANN001
            return fake_process

        client = CodexAppServerClient(command=["/usr/bin/codex", "app-server"], popen_factory=fake_popen)
        expected = {
            "userAgent": "book-workbench/0.130.0",
            "codexHome": "/tmp/codex-home",
            "platformFamily": "unix",
            "platformOs": "macos",
        }
        with mock.patch.object(client, "_read_response", return_value=expected):
            health = client.health()

        request = json.loads(fake_process.stdin.getvalue().decode("utf-8"))
        self.assertTrue(health["ok"], health)
        self.assertEqual(health["response"], expected)
        self.assertEqual(request["method"], "initialize")
        self.assertEqual(request["params"]["clientInfo"]["name"], "book-workbench")
        self.assertEqual(request["params"]["capabilities"]["experimentalApi"], True)
        self.assertTrue(fake_process.terminated)

    def test_probe_turn_routes_server_file_change_approval(self) -> None:
        client = CodexAppServerClient(command=["/usr/bin/codex", "app-server"], timeout_seconds=1)
        approvals: list[dict] = []
        notifications: list[dict] = []
        server_requests: list[dict] = []
        deltas: list[str] = []
        final_messages: list[str] = []

        messages = iter(
            [
                {
                    "id": "approval-1",
                    "method": "item/fileChange/requestApproval",
                    "params": {"threadId": "thread-1", "turnId": "turn-1"},
                },
                {
                    "method": "item/agentMessage/delta",
                    "params": {"threadId": "thread-1", "turnId": "turn-1", "delta": "done"},
                },
                {
                    "method": "item/completed",
                    "params": {"item": {"type": "agentMessage", "text": "done"}},
                },
                {
                    "method": "turn/completed",
                    "params": {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "completed"}},
                },
            ]
        )

        class FakeSession:
            timeout_seconds = 1

            def read_message(self, process, deadline, stderr_chunks):  # noqa: ANN001
                try:
                    return next(messages)
                except StopIteration:
                    return None

        fake_process = FakeProcess()
        with mock.patch.object(client, "_send_message") as send_message:
            completed = client._collect_turn(
                fake_process,
                FakeSession(),
                stderr_chunks=[],
                notifications=notifications,
                server_requests=server_requests,
                approvals=approvals,
                deltas=deltas,
                final_messages=final_messages,
                thread_id="thread-1",
                turn_id="turn-1",
                approval_handler=lambda message: {"decision": "decline", "reason": "runtime_policy"},
            )

        self.assertTrue(completed)
        self.assertEqual(server_requests[0]["method"], "item/fileChange/requestApproval")
        self.assertEqual(approvals[0]["response"]["decision"], "decline")
        self.assertEqual(final_messages, ["done"])
        response_payloads = [call.args[1] for call in send_message.call_args_list]
        self.assertEqual(response_payloads[0]["id"], "approval-1")
        self.assertEqual(response_payloads[0]["result"]["reason"], "runtime_policy")

    def test_list_skills_uses_explicit_cwd_without_global_install(self) -> None:
        fake_process = FakeProcess()
        client = CodexAppServerClient(command=["/usr/bin/codex", "app-server"], popen_factory=lambda *a, **k: fake_process)

        responses = iter(
            [
                {"id": 2, "result": {"userAgent": "book-workbench/0.130.0"}},
                {
                    "id": 3,
                    "result": {
                        "data": [
                            {
                                "cwd": "/tmp/project",
                                "errors": [],
                                "skills": [
                                    {
                                        "name": "revise-with-annotations",
                                        "scope": "repo",
                                        "path": "/tmp/project/.codex/skills/revise-with-annotations/SKILL.md",
                                    }
                                ],
                            }
                        ]
                    },
                },
            ]
        )

        class FakeSession:
            def __init__(self, timeout_seconds):  # noqa: ANN001
                self.timeout_seconds = timeout_seconds
                self._next = 1

            def next_id(self):
                self._next += 1
                return self._next

            def wait_for_response(self, process, request_id, stderr_chunks, notifications):  # noqa: ANN001
                return next(responses)

        with mock.patch("book_workbench.codex_client._JsonRpcSession", FakeSession):
            result = client.list_skills(cwds=["/tmp/project"])

        requests = [json.loads(line) for line in fake_process.stdin.getvalue().decode("utf-8").splitlines()]
        self.assertTrue(result["ok"], result)
        self.assertEqual(requests[1]["method"], "skills/list")
        self.assertEqual(requests[1]["params"]["cwds"], [str(Path("/tmp/project").resolve())])
        self.assertEqual(result["response"]["data"][0]["skills"][0]["scope"], "repo")
        self.assertIn("/.codex/skills/", result["response"]["data"][0]["skills"][0]["path"])

    def test_process_cwd_avoids_project_local_config_when_explicit_cwd_is_passed(self) -> None:
        root = Path(self.id().replace(".", "_"))
        with mock.patch("book_workbench.codex_client.Path.home", return_value=Path("/tmp/book-workbench-home")):
            plain = CodexAppServerClient(command=["/usr/bin/codex", "app-server"], cwd=root)
            with_config = CodexAppServerClient(command=["/usr/bin/codex", "app-server"], cwd=ROOT)

            self.assertEqual(plain._process_cwd(), root.resolve())
            self.assertEqual(with_config._process_cwd(), Path("/tmp/book-workbench-home"))

    def test_json_turn_parses_and_validates_generic_skill_output(self) -> None:
        client = CodexAppServerClient(command=["/usr/bin/codex", "app-server"])
        with mock.patch.object(
            client,
            "run_probe_turn",
            return_value={
                "ok": True,
                "finalText": '{"id":"RP-test","summary":"ok","rules":[]}',
            },
        ) as run_probe:
            result = client.run_json_turn(
                prompt="return rule proposal",
                json_validator=lambda value: {"valid": value["id"] == "RP-test", "issues": []},
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["jsonObject"]["id"], "RP-test")
        self.assertEqual(result["jsonValidation"], {"valid": True, "issues": []})
        self.assertIn("Return one JSON object only", run_probe.call_args.kwargs["developer_instructions"])

    def test_json_turn_marks_non_json_output_invalid(self) -> None:
        client = CodexAppServerClient(command=["/usr/bin/codex", "app-server"])
        with mock.patch.object(client, "run_probe_turn", return_value={"ok": True, "finalText": "not json"}):
            result = client.run_json_turn(prompt="return rule proposal")

        self.assertFalse(result["ok"])
        self.assertIsNone(result["jsonObject"])
        self.assertEqual(result["jsonValidation"]["issues"][0]["code"], "invalid_json")

    def test_patch_proposal_turn_parses_and_validates_json_without_writing(self) -> None:
        client = CodexAppServerClient(command=["/usr/bin/codex", "app-server"])
        with mock.patch.object(
            client,
            "run_probe_turn",
            return_value={
                "ok": True,
                "finalText": '{"id":"PP-test","summary":"ok","sourceAnnotations":["USER-test"],"rulesUsed":[],"changes":[]}',
            },
        ) as run_probe:
            result = client.run_patch_proposal_turn(
                prompt="return patch",
                patch_validator=lambda proposal: {"valid": proposal["id"] == "PP-test", "issues": []},
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["patchProposal"]["id"], "PP-test")
        self.assertEqual(result["patchValidation"], {"valid": True, "issues": []})
        self.assertIn("PatchProposal JSON", run_probe.call_args.kwargs["developer_instructions"])

    def test_patch_proposal_turn_marks_non_json_output_invalid(self) -> None:
        client = CodexAppServerClient(command=["/usr/bin/codex", "app-server"])
        with mock.patch.object(client, "run_probe_turn", return_value={"ok": True, "finalText": "not json"}):
            result = client.run_patch_proposal_turn(prompt="return patch")

        self.assertFalse(result["ok"])
        self.assertIsNone(result["patchProposal"])
        self.assertEqual(result["patchValidation"]["issues"][0]["code"], "invalid_json")

    def test_read_message_drains_multiple_json_lines_from_one_chunk(self) -> None:
        session = _JsonRpcSession(timeout_seconds=1)
        session._stdout_buffer = (
            b'{"method":"thread/started","params":{}}\n'
            b'{"id":3,"result":{"thread":{"id":"thread-1"}}}\n'
        )
        fake_process = FakeProcess()

        first = session.read_message(fake_process, 9999999999, [])
        second = session.read_message(fake_process, 9999999999, [])

        self.assertEqual(first["method"], "thread/started")
        self.assertEqual(second["id"], 3)

    def test_missing_executable_reports_unhealthy_status(self) -> None:
        with mock.patch("book_workbench.codex_client.shutil.which", return_value=None):
            health = CodexAppServerClient(command=["definitely-missing-codex"]).health()

        self.assertFalse(health["ok"])
        self.assertIn("Executable not found", health["error"])

    def test_malformed_response_reports_unhealthy_status(self) -> None:
        fake_process = FakeProcess()
        client = CodexAppServerClient(command=["/usr/bin/codex", "app-server"], popen_factory=lambda *a, **k: fake_process)
        with mock.patch.object(client, "_read_response", side_effect=RuntimeError("bad protocol")):
            health = client.health()

        self.assertFalse(health["ok"])
        self.assertEqual(health["error"], "bad protocol")
        self.assertTrue(fake_process.terminated)

    def test_timeout_reports_unhealthy_status(self) -> None:
        fake_process = FakeProcess()
        client = CodexAppServerClient(command=["/usr/bin/codex", "app-server"], popen_factory=lambda *a, **k: fake_process)
        with mock.patch.object(client, "_read_response", side_effect=TimeoutError("Timed out waiting")):
            health = client.health()

        self.assertFalse(health["ok"])
        self.assertIn("Timed out", health["error"])
        self.assertTrue(fake_process.terminated)


if __name__ == "__main__":
    unittest.main()
