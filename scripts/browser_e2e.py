from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from book_workbench.app_server import create_server
from tests.test_fixtures import write_black_rain_fixture


class FakeCodexClient:
    def health(self) -> dict:
        return {"ok": True, "command": ["fake-codex", "app-server"], "error": None, "notifications": [], "durationMs": 1}


def main() -> int:
    artifacts = ROOT / ".omx" / "evidence" / "browser-e2e"
    if artifacts.exists():
        shutil.rmtree(artifacts)
    artifacts.mkdir(parents=True)
    console_messages: list[dict[str, str]] = []
    page_errors: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "workspace"
        workspace.mkdir()
        server = create_server(
            None,
            workspace_root=workspace,
            port=0,
            codex_client=FakeCodexClient(),
            local_token="test-token",
            quiet=True,
        )
        host, port = server.server_address[:2]
        base_url = f"http://{host}:{port}/"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            from playwright.sync_api import sync_playwright, expect

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("console", lambda msg: console_messages.append({"type": msg.type, "text": msg.text}))
                page.on("pageerror", lambda exc: page_errors.append(str(exc)))
                page.goto(base_url, wait_until="networkidle")

                expect(page.get_by_test_id("empty-workspace")).to_be_visible(timeout=5000)
                assert "黑雨之后" not in page.content(), "empty workspace must not preload demo novel"
                assert "正在连接本地 Runtime" not in page.content(), "startup must not show stale connecting state"
                page.screenshot(path=str(artifacts / "01-empty-workspace.png"), full_page=True)

                page.get_by_test_id("open-new-project-modal").click()
                expect(page.get_by_test_id("new-project-modal")).to_be_visible()
                page.locator("#projectTitleInput").fill("我的第一本书")
                page.locator("#projectSlugInput").fill("my-first-book")
                page.locator("#submitProjectBtn").click()
                expect(page.get_by_test_id("project-card").filter(has_text="我的第一本书")).to_be_visible(timeout=5000)
                page.screenshot(path=str(artifacts / "02-created-project-listed.png"), full_page=True)

                page.get_by_test_id("project-card").filter(has_text="我的第一本书").click()
                expect(page.get_by_text("章节列表")).to_be_visible(timeout=5000)
                expect(page.locator("#chapterRows").get_by_text("chapters/ch01.md")).to_be_visible()
                page.get_by_text("打开 ›").first.click()
                expect(page.locator("#chapterSelect")).to_contain_text("第一章", timeout=5000)
                page.screenshot(path=str(artifacts / "03-open-created-project.png"), full_page=True)

                # Runtime-backed annotation creation through the UI. The default created project has an empty first block,
                # so create a fresh fixture for the manuscript editing e2e flow and open it from the same workspace.
                write_black_rain_fixture(workspace / "black-rain-after", init_git=True)
                page.goto(base_url, wait_until="networkidle")
                expect(page.get_by_test_id("project-card").filter(has_text="黑雨之后")).to_be_visible(timeout=5000)
                page.evaluate("() => window.BookWorkbench.openProject('black-rain-after')")
                page.wait_for_function("() => window.BookWorkbench.state?.project?.summary?.slug === 'black-rain-after' && document.querySelector('#chapterRows')?.textContent.includes('chapters/ch05.md')")
                page.locator("#chapterRows").get_by_text("chapters/ch05.md").click()
                expect(page.locator("#chapterSelect")).to_contain_text("第五章", timeout=5000)
                before_chapter = (workspace / "black-rain-after" / "chapters" / "ch05.md").read_text(encoding="utf-8")
                page.locator('.paragraph[data-block="ch05-p018"] .add-annotation-btn').click()
                expect(page.get_by_test_id("annotation-modal")).to_be_visible()
                page.locator("#annotationSelectedInput").fill("我的心里很复杂，我想起了过去的种种，内心充满了矛盾和挣扎。")
                page.locator("#annotationBodyInput").fill("这里太像 AI 了，不要解释内心，用动作表现。")
                page.locator("#submitAnnotationBtn").click()
                expect(page.locator("#annotationPanel").get_by_text("AN-1000")).to_be_visible(timeout=5000)
                after_annotation = (workspace / "black-rain-after" / "chapters" / "ch05.md").read_text(encoding="utf-8")
                assert before_chapter == after_annotation, "annotation sidecar flow must not mutate chapter body"

                page.locator("#reviseCurrentBtn").click()
                expect(page.locator("#view-diff")).to_be_visible(timeout=5000)
                expect(page.locator("#patchValidity")).to_contain_text("通过", timeout=5000)
                assert (workspace / "black-rain-after" / "chapters" / "ch05.md").read_text(encoding="utf-8") == before_chapter, "AI revise before accept must not mutate chapter"
                page.screenshot(path=str(artifacts / "04-diff-preview-before-accept.png"), full_page=True)

                before_commits = int(subprocess.check_output(["git", "rev-list", "--count", "HEAD"], cwd=workspace / "black-rain-after", text=True).strip())
                page.locator("#acceptPatchBtn").click()
                expect(page.locator("#view-editor")).to_be_visible(timeout=5000)
                final_text = (workspace / "black-rain-after" / "chapters" / "ch05.md").read_text(encoding="utf-8")
                after_commits = int(subprocess.check_output(["git", "rev-list", "--count", "HEAD"], cwd=workspace / "black-rain-after", text=True).strip())
                assert "纸杯沿一点点捏扁" in final_text, "accepted patch must apply expected text"
                assert after_commits == before_commits + 1, "accepted patch must create git commit"
                page.screenshot(path=str(artifacts / "05-after-accept-commit.png"), full_page=True)
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    bad_console = [m for m in console_messages if m["type"] in {"error"}]
    (artifacts / "console.json").write_text(json.dumps(console_messages, ensure_ascii=False, indent=2), encoding="utf-8")
    (artifacts / "page-errors.json").write_text(json.dumps(page_errors, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {"ok": not bad_console and not page_errors, "consoleErrors": bad_console, "pageErrors": page_errors, "artifacts": str(artifacts)}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
