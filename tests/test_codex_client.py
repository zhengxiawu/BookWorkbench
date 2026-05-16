from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from book_workbench.codex_client import CodexAppServerClient


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
