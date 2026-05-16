"""Small Codex app-server health client.

The Codex app-server currently speaks newline-delimited JSON-RPC over stdio.
BookWorkbench only needs a narrow, bounded health seam for the local app: start
the server, send ``initialize``, read the first matching response, then stop the
process.  Full thread/turn orchestration can be layered on this module later
without coupling the browser UI to subprocess details.
"""

from __future__ import annotations

import json
import os
import selectors
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import __version__


DEFAULT_CLIENT_INFO = {
    "name": "book-workbench",
    "version": __version__,
    "title": "BookWorkbench",
}


class CodexClientError(RuntimeError):
    """Raised for malformed local app-server responses."""


@dataclass(frozen=True)
class CodexHealth:
    ok: bool
    command: List[str]
    error: Optional[str] = None
    response: Optional[Dict[str, Any]] = None
    stderr: str = ""
    notifications: List[Dict[str, Any]] | None = None
    duration_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "command": self.command,
            "error": self.error,
            "response": self.response,
            "stderr": self.stderr,
            "notifications": self.notifications or [],
            "durationMs": self.duration_ms,
        }


PopenFactory = Callable[..., subprocess.Popen]


class CodexAppServerClient:
    """Bounded JSON-RPC client for Codex app-server initialize checks."""

    def __init__(
        self,
        *,
        command: Sequence[str] | None = None,
        timeout_seconds: float = 5.0,
        cwd: str | Path | None = None,
        popen_factory: PopenFactory | None = None,
    ) -> None:
        self.command = list(command or ["codex", "app-server"])
        self.timeout_seconds = timeout_seconds
        self.cwd = Path(cwd).resolve() if cwd is not None else None
        self._popen_factory = popen_factory or subprocess.Popen

    def health(self) -> Dict[str, Any]:
        """Return a JSON-serializable initialize health report.

        This method intentionally catches operational failures so UI and CLI
        callers can present a status panel instead of crashing when Codex is not
        installed or the protocol changes.
        """

        started = time.monotonic()
        if shutil.which(self.command[0]) is None and os.sep not in self.command[0]:
            return CodexHealth(
                ok=False,
                command=self.command,
                error=f"Executable not found: {self.command[0]}",
                duration_ms=self._elapsed_ms(started),
            ).to_dict()

        process: subprocess.Popen | None = None
        stderr_chunks: List[str] = []
        notifications: List[Dict[str, Any]] = []
        try:
            process = self._popen_factory(
                self.command,
                cwd=str(self.cwd) if self.cwd is not None else None,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
            )
            request = self.initialize_request()
            assert process.stdin is not None
            process.stdin.write((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
            process.stdin.flush()

            response = self._read_response(process, stderr_chunks, notifications, request_id=request["id"])
            return CodexHealth(
                ok=True,
                command=self.command,
                response=response,
                stderr="".join(stderr_chunks),
                notifications=notifications,
                duration_ms=self._elapsed_ms(started),
            ).to_dict()
        except Exception as exc:  # defensive local integration boundary
            return CodexHealth(
                ok=False,
                command=self.command,
                error=str(exc),
                stderr="".join(stderr_chunks),
                notifications=notifications,
                duration_ms=self._elapsed_ms(started),
            ).to_dict()
        finally:
            self._stop_process(process)

    def initialize_request(self) -> Dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": dict(DEFAULT_CLIENT_INFO),
                "capabilities": {"experimentalApi": True},
            },
        }

    def _read_response(
        self,
        process: subprocess.Popen,
        stderr_chunks: List[str],
        notifications: List[Dict[str, Any]],
        *,
        request_id: int,
    ) -> Dict[str, Any]:
        if process.stdout is None:
            raise CodexClientError("Codex app-server stdout is unavailable.")
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        if process.stderr is not None:
            selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        deadline = time.monotonic() + self.timeout_seconds
        stdout_buffer = b""

        while time.monotonic() < deadline:
            events = selector.select(max(0.05, min(0.25, deadline - time.monotonic())))
            if not events and process.poll() is not None and not stdout_buffer:
                raise CodexClientError(f"Codex app-server exited before initialize response: {process.returncode}")
            for key, _ in events:
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:
                    continue
                if key.data == "stderr":
                    stderr_chunks.append(chunk.decode("utf-8", errors="replace"))
                    continue
                stdout_buffer += chunk
                while b"\n" in stdout_buffer:
                    raw_line, stdout_buffer = stdout_buffer.split(b"\n", 1)
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    message = self._parse_message(line)
                    if message.get("id") == request_id:
                        if "error" in message:
                            raise CodexClientError(f"Codex initialize error: {message['error']}")
                        result = message.get("result")
                        if not isinstance(result, dict):
                            raise CodexClientError("Codex initialize result is not an object.")
                        return result
                    if "method" in message:
                        notifications.append(message)
        raise TimeoutError(f"Timed out waiting for Codex app-server initialize response after {self.timeout_seconds:.1f}s.")

    @staticmethod
    def _parse_message(line: str) -> Dict[str, Any]:
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CodexClientError(f"Malformed Codex app-server JSON: {exc}") from exc
        if not isinstance(message, dict):
            raise CodexClientError("Codex app-server message is not an object.")
        return message

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return int((time.monotonic() - started) * 1000)

    @staticmethod
    def _stop_process(process: subprocess.Popen | None) -> None:
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=1)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=1)
            except Exception:
                pass
