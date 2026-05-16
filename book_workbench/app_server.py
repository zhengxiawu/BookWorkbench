"""Stdlib local web app for BookWorkbench.

The app is deliberately thin: browser actions call the same
``RuntimeOrchestrator`` methods as the CLI, so all manuscript writes still pass
through patch validation, preview, apply, and audit logging.
"""

from __future__ import annotations

import json
import secrets
import threading
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Mapping
from urllib.parse import parse_qs, unquote, urlparse

from .annotation_engine import annotation_to_dict, classification_summary, list_annotations
from .audit import AuditLog
from .codex_client import CodexAppServerClient
from .project import ProjectLoadError, load_project, safe_chapter_path
from .runtime import RuntimeErrorBase, RuntimeOrchestrator


APP_TITLE = "BookWorkbench Local App"


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>BookWorkbench Local App</title>
  <style>
    :root { color-scheme: light dark; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    body { margin: 0; background: #f4f1eb; color: #211b16; }
    header { padding: 18px 24px; background: #211b16; color: #fff6e8; }
    main { display: grid; grid-template-columns: minmax(280px, 360px) 1fr; gap: 16px; padding: 16px; }
    section { background: #fffaf1; border: 1px solid #d8cab5; border-radius: 12px; padding: 14px; box-shadow: 0 1px 2px #0001; }
    button, select { border: 1px solid #9f8060; border-radius: 8px; padding: 8px 10px; background: #fff; color: #211b16; }
    button { cursor: pointer; margin: 3px 4px 3px 0; background: #5a3825; color: white; }
    button.secondary { background: #756a5d; }
    pre { overflow: auto; white-space: pre-wrap; background: #1d1b19; color: #fff4df; border-radius: 8px; padding: 12px; min-height: 80px; }
    .status-ok { color: #0a7f35; font-weight: 700; }
    .status-bad { color: #a32222; font-weight: 700; }
    .pill { display: inline-block; padding: 2px 8px; margin: 2px; border-radius: 999px; background: #eadbc5; }
    @media (max-width: 900px) { main { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <header>
    <h1>BookWorkbench Local App</h1>
    <div id="health">正在连接本地 Runtime 与 Codex app-server…</div>
  </header>
  <main>
    <section>
      <h2>项目</h2>
      <button onclick="loadProject()">刷新项目</button>
      <button class="secondary" onclick="loadAudit()">查看审计</button>
      <div id="chapters"></div>
      <h3>批注</h3>
      <div id="annotations"></div>
    </section>
    <section>
      <h2>实际操作测试</h2>
      <p>所有写入都通过 Runtime 的 PatchProposal 校验、差异预览与安全应用。</p>
      <button onclick="runRevise()">1. 运行 revise-with-annotations</button>
      <button onclick="previewPatch()">2. 预览 Diff</button>
      <button onclick="applyPatch()">3. 应用 Patch</button>
      <button class="secondary" onclick="loadChapter('chapters/ch05.md')">读取第五章</button>
      <h3>章节 / Diff / 结果</h3>
      <pre id="output"></pre>
      <h3>审计</h3>
      <pre id="audit"></pre>
    </section>
  </main>
  <script>
    const BOOKWORKBENCH_TOKEN = __BOOKWORKBENCH_TOKEN__;
    let lastPatch = null;
    async function api(path, options = {}) {
      const headers = {
        "Content-Type": "application/json",
        "X-BookWorkbench-Token": BOOKWORKBENCH_TOKEN,
        ...(options.headers || {})
      };
      const response = await fetch(path, {
        ...options,
        headers
      });
      const text = await response.text();
      let payload;
      try { payload = text ? JSON.parse(text) : {}; } catch (_) { payload = { raw: text }; }
      if (!response.ok) throw new Error(payload.error || response.statusText);
      return payload;
    }
    function dump(id, payload) { document.getElementById(id).textContent = typeof payload === "string" ? payload : JSON.stringify(payload, null, 2); }
    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[char]));
    }
    async function loadHealth() {
      const health = await api("/api/health");
      const codex = health.codex || {};
      document.getElementById("health").innerHTML =
        `Runtime <span class="status-ok">OK</span> · Codex app-server ` +
        `<span class="${codex.ok ? "status-ok" : "status-bad"}">${codex.ok ? "OK" : "未连接"}</span>`;
    }
    async function loadProject() {
      const project = await api("/api/project");
      const chapterButtons = Object.keys(project.blocks).map(file => {
        const encoded = encodeURIComponent(file);
        const label = `${file} (${project.chapterStatus[file] || "draft"})`;
        return `<button class="secondary chapter-button" data-file="${escapeHtml(encoded)}">${escapeHtml(label)}</button>`;
      });
      document.getElementById("chapters").innerHTML = chapterButtons.join("<br>");
      document.querySelectorAll(".chapter-button").forEach(button => {
        button.addEventListener("click", () => loadChapter(decodeURIComponent(button.dataset.file)));
      });
      const annotations = await api("/api/annotations?include_resolved=1");
      document.getElementById("annotations").innerHTML = annotations.annotations
        .map(a => `<div class="pill">${escapeHtml(a.id)}: ${escapeHtml(a.text)}</div>`)
        .join("");
      dump("output", project);
    }
    async function loadChapter(file) {
      dump("output", await api("/api/chapters/" + encodeURIComponent(file)));
    }
    async function runRevise() {
      const result = await api("/api/skills/run", { method: "POST", body: JSON.stringify({ skill: "revise-with-annotations", annotationIds: ["AN-041"] }) });
      lastPatch = result.output;
      dump("output", result);
    }
    async function previewPatch() {
      if (!lastPatch) await runRevise();
      dump("output", await api("/api/patch/preview", { method: "POST", body: JSON.stringify({ patch: lastPatch }) }));
    }
    async function applyPatch() {
      if (!lastPatch) await runRevise();
      dump("output", await api("/api/patch/apply", { method: "POST", body: JSON.stringify({ patch: lastPatch }) }));
      await loadAudit();
    }
    async function loadAudit() { dump("audit", await api("/api/audit")); }
    loadHealth().then(loadProject).catch(error => dump("output", { error: String(error) }));
  </script>
</body>
</html>
"""


class RuntimeWebApp:
    def __init__(
        self,
        project_root: str | Path,
        *,
        builtin_skills_root: str | Path | None = None,
        codex_client: CodexAppServerClient | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.runtime = RuntimeOrchestrator(self.project_root, builtin_skills_root=builtin_skills_root)
        self.codex_client = codex_client or CodexAppServerClient(cwd=self.project_root)
        self._lock = threading.RLock()

    def health(self) -> Dict[str, Any]:
        with self._lock:
            project = self.runtime.inspect()
        return {
            "app": {"name": APP_TITLE, "ok": True},
            "runtime": {
                "ok": True,
                "projectRoot": project["root"],
                "chapters": len(project["blocks"]),
                "annotations": len(project["annotations"]),
                "skills": sorted(project["skills"]),
            },
            "codex": self.codex_client.health(),
        }

    def project(self) -> Dict[str, Any]:
        with self._lock:
            return self.runtime.inspect()

    def annotations(self, query: Mapping[str, list[str]]) -> Dict[str, Any]:
        context = load_project(self.project_root)
        annotations = list_annotations(
            context,
            file_path=_first(query, "file"),
            status=_first(query, "status"),
            annotation_type=_first(query, "type"),
            include_resolved=_bool_query(query, "include_resolved") or _bool_query(query, "includeResolved"),
        )
        return {
            "annotations": [annotation_to_dict(item) for item in annotations],
            "summary": classification_summary(annotations),
        }

    def chapter(self, file_path: str) -> Dict[str, Any]:
        context = load_project(self.project_root)
        rel_path = unquote(file_path)
        safe_chapter_path(context.root, rel_path)
        blocks = context.blocks.get(rel_path)
        if blocks is None:
            raise ProjectLoadError(f"Unknown chapter: {rel_path}")
        ordered_blocks = dict(sorted((block_id, asdict(block)) for block_id, block in blocks.items()))
        return {
            "file": rel_path,
            "status": context.status_for_file(rel_path),
            "blockIds": list(ordered_blocks),
            "blocks": ordered_blocks,
        }

    def run_skill(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        skill = _required_string(payload, "skill")
        annotation_ids = (
            payload.get("annotationIds")
            or payload.get("annotation_ids")
            or payload.get("annotations")
            or payload.get("annotation")
        )
        if isinstance(annotation_ids, str):
            annotation_ids = [annotation_ids]
        if annotation_ids is not None and not isinstance(annotation_ids, list):
            raise ValueError("annotationIds must be a string or array of strings.")
        with self._lock:
            return self.runtime.run_skill(
                skill,
                annotation_ids=annotation_ids,
                scope_file=_optional_string(payload, "file") or _optional_string(payload, "scopeFile"),
            )

    def preview_patch(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        patch = payload.get("patch", payload)
        with self._lock:
            return self.runtime.preview_patch(patch, allow_reviewed=_bool_payload(payload, "allowReviewed") or _bool_payload(payload, "allow_reviewed"))

    def apply_patch(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        patch = payload.get("patch", payload)
        with self._lock:
            return self.runtime.accept_patch(patch, allow_reviewed=_bool_payload(payload, "allowReviewed") or _bool_payload(payload, "allow_reviewed"))

    def audit(self) -> Dict[str, Any]:
        return {"events": AuditLog(self.project_root).read()}


class BookWorkbenchHandler(BaseHTTPRequestHandler):
    server_version = "BookWorkbenchHTTP/0.1"

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        try:
            if not self._host_allowed():
                return
            if parsed.path == "/":
                self._send_html(self._index_html())
            elif parsed.path == "/api/health":
                self._send_json(self.app.health())
            elif parsed.path == "/api/project":
                self._send_json(self.app.project())
            elif parsed.path == "/api/annotations":
                self._send_json(self.app.annotations(parse_qs(parsed.query)))
            elif parsed.path == "/api/audit":
                self._send_json(self.app.audit())
            elif parsed.path == "/api/chapters":
                query = parse_qs(parsed.query)
                file_path = _first(query, "file")
                if not file_path:
                    self._send_json({"chapters": sorted(self.app.project()["blocks"])})
                else:
                    self._send_json(self.app.chapter(file_path))
            elif parsed.path.startswith("/api/chapters/"):
                self._send_json(self.app.chapter(parsed.path[len("/api/chapters/") :]))
            else:
                self._send_error(HTTPStatus.NOT_FOUND, f"Unknown endpoint: {parsed.path}")
        except Exception as exc:
            self._send_exception(exc)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        try:
            if not self._host_allowed():
                return
            if not self._origin_allowed():
                return
            if not self._json_content_type_allowed():
                return
            if not self._token_allowed(parsed):
                return
            payload = self._read_json()
            if parsed.path == "/api/skills/run":
                self._send_json(self.app.run_skill(payload))
            elif parsed.path in {"/api/patch/preview", "/api/patches/preview"}:
                self._send_json(self.app.preview_patch(payload))
            elif parsed.path in {"/api/patch/apply", "/api/patches/apply"}:
                self._send_json(self.app.apply_patch(payload))
            elif parsed.path == "/api/codex/health":
                self._send_json({"codex": self.app.codex_client.health()})
            else:
                self._send_error(HTTPStatus.NOT_FOUND, f"Unknown endpoint: {parsed.path}")
        except Exception as exc:
            self._send_exception(exc)

    @property
    def app(self) -> RuntimeWebApp:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        if getattr(self.server, "quiet", False):  # type: ignore[attr-defined]
            return
        super().log_message(format, *args)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Request JSON body must be an object.")
        return payload

    def _index_html(self) -> str:
        token = getattr(self.server, "local_token", "")  # type: ignore[attr-defined]
        return INDEX_HTML.replace("__BOOKWORKBENCH_TOKEN__", json.dumps(token))

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message, "status": status.value}, status=status)

    def _send_exception(self, exc: Exception) -> None:
        if isinstance(exc, (ValueError, KeyError, json.JSONDecodeError, ProjectLoadError, RuntimeErrorBase)):
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
        else:
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def _host_allowed(self) -> bool:
        raw_host = self.headers.get("Host")
        if not raw_host:
            self._send_error(HTTPStatus.FORBIDDEN, "Missing Host header.")
            return False
        try:
            parsed = urlparse(f"//{raw_host}")
        except ValueError:
            self._send_error(HTTPStatus.FORBIDDEN, "Invalid Host header.")
            return False
        hostname = parsed.hostname
        if not hostname:
            self._send_error(HTTPStatus.FORBIDDEN, "Invalid Host header.")
            return False
        port = parsed.port
        server_host, server_port = self.server.server_address[:2]
        allowed_hosts = {"127.0.0.1", "localhost", "::1", str(server_host)}
        if hostname not in allowed_hosts:
            self._send_error(HTTPStatus.FORBIDDEN, "Host header is not allowed.")
            return False
        if port is not None and int(port) != int(server_port):
            self._send_error(HTTPStatus.FORBIDDEN, "Host port is not allowed.")
            return False
        return True

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        expected = f"http://{self.headers.get('Host')}"
        if origin == expected:
            return True
        self._send_error(HTTPStatus.FORBIDDEN, "Cross-origin POST requests are not allowed.")
        return False

    def _json_content_type_allowed(self) -> bool:
        if self.headers.get_content_type() == "application/json":
            return True
        self._send_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "POST requests must use Content-Type: application/json.")
        return False

    def _token_allowed(self, parsed_path) -> bool:  # noqa: ANN001 - urlparse return type varies by Python version
        expected = getattr(self.server, "local_token", "")  # type: ignore[attr-defined]
        if not expected:
            return True
        provided = self.headers.get("X-BookWorkbench-Token", "")
        if not provided:
            auth_header = self.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                provided = auth_header[len("Bearer ") :]
        if secrets.compare_digest(provided, expected):
            return True
        self._send_error(HTTPStatus.FORBIDDEN, "Missing or invalid local API token.")
        return False


def create_server(
    project_root: str | Path,
    *,
    builtin_skills_root: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    codex_client: CodexAppServerClient | None = None,
    local_token: str | None = None,
    quiet: bool = False,
) -> ThreadingHTTPServer:
    app = RuntimeWebApp(project_root, builtin_skills_root=builtin_skills_root, codex_client=codex_client)
    server = ThreadingHTTPServer((host, port), BookWorkbenchHandler)
    server.app = app  # type: ignore[attr-defined]
    server.local_token = local_token if local_token is not None else secrets.token_urlsafe(24)  # type: ignore[attr-defined]
    server.quiet = quiet  # type: ignore[attr-defined]
    return server


def serve(
    project_root: str | Path,
    *,
    builtin_skills_root: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
) -> None:
    server = create_server(project_root, builtin_skills_root=builtin_skills_root, host=host, port=port)
    actual_host, actual_port = server.server_address[:2]
    url = f"http://{actual_host}:{actual_port}/"
    print(f"{APP_TITLE} running at {url}", flush=True)
    print(f"Local API token: {server.local_token}", flush=True)  # type: ignore[attr-defined]
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping BookWorkbench Local App")
    finally:
        server.server_close()


def _first(query: Mapping[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _bool_query(query: Mapping[str, list[str]], key: str) -> bool:
    value = _first(query, key)
    return value is not None and value.lower() not in {"0", "false", "no", "off", ""}


def _bool_payload(payload: Mapping[str, Any], key: str) -> bool:
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() not in {"0", "false", "no", "off", ""}
    return bool(value)


def _optional_string(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string.")
    return value


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = _optional_string(payload, key)
    if not value:
        raise ValueError(f"Missing required field: {key}")
    return value
