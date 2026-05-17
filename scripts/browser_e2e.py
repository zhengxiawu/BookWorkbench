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
    def __init__(self) -> None:
        self.patch_calls = 0

    def health(self) -> dict:
        return {"ok": True, "command": ["fake-codex", "app-server"], "error": None, "notifications": [], "durationMs": 1}

    def list_skills(self, *, cwds, force_reload=True):  # noqa: ANN001
        root = Path(cwds[0])
        return {
            "ok": True,
            "response": {
                "data": [
                    {
                        "cwd": root.as_posix(),
                        "errors": [],
                        "skills": [
                            {
                                "name": "revise-with-annotations",
                                "scope": "repo",
                                "path": (root / ".codex" / "skills" / "revise-with-annotations" / "SKILL.md").as_posix(),
                            }
                        ],
                    }
                ]
            },
        }

    def run_patch_proposal_turn(self, **kwargs):  # noqa: ANN003
        self.patch_calls += 1
        prompt = kwargs.get("prompt", "")
        if "AN-001" in prompt:
            proposal = {
                "id": "PP-e2e-codex-user",
                "summary": "browser e2e codex main path",
                "sourceAnnotations": ["AN-001"],
                "rulesUsed": ["R-001"],
                "changes": [
                    {
                        "file": "chapters/ch01.md",
                        "targetBlockId": "ch01-p001",
                        "operation": "replace_block",
                        "beforeHash": "sha256:3d7bdd",
                        "afterText": "我站在门口，把那封信翻到背面，指尖停在没有署名的空白处。",
                        "reason": "fake codex main path proposal for browser e2e",
                    }
                ],
            }
        elif "AN-1000" in prompt:
            proposal = {
                "id": "PP-e2e-codex-invalid",
                "summary": "force fallback for fixture path",
                "sourceAnnotations": ["AN-1000"],
                "rulesUsed": [],
                "changes": [],
            }
        else:
            proposal = {
                "id": "PP-e2e-codex",
                "summary": "browser e2e codex seam",
                "sourceAnnotations": ["AN-041"],
                "rulesUsed": ["R-018"],
                "changes": [
                    {
                        "file": "chapters/ch05.md",
                        "targetBlockId": "ch05-p018",
                        "operation": "replace_block",
                        "beforeHash": "sha256:a91f3c",
                        "afterText": "我坐在审讯室里，盯着对面的男人。他没有看我，只把纸杯沿一点点捏扁。",
                        "reason": "fake codex seam proposal for browser e2e",
                    }
                ],
            }
        validation = kwargs["patch_validator"](proposal)
        return {
            "ok": validation["valid"],
            "threadId": "thread-e2e",
            "turnId": "turn-e2e",
            "notifications": [{"method": "thread/started"}, {"method": "turn/started"}, {"method": "item/completed"}, {"method": "turn/completed"}],
            "approvals": [],
            "serverRequests": [],
            "patchProposal": proposal,
            "patchValidation": validation,
        }


def git_count(root: Path) -> int:
    return int(subprocess.check_output(["git", "rev-list", "--count", "HEAD"], cwd=root, text=True).strip())


def main() -> int:
    artifacts = ROOT / ".omx" / "evidence" / "browser-e2e"
    if artifacts.exists():
        shutil.rmtree(artifacts)
    artifacts.mkdir(parents=True)
    console_messages: list[dict[str, str]] = []
    page_errors: list[str] = []
    flow_report: dict[str, object] = {"computerUseToolAvailable": False, "harness": "Playwright browser actions"}

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

                # Whole-app user flow on a user-created book: create -> open -> discussion -> annotation -> AI patch -> apply.
                page.get_by_test_id("open-new-project-modal").click()
                expect(page.get_by_test_id("new-project-modal")).to_be_visible()
                page.locator("#projectTitleInput").fill("雾中来信")
                page.locator("#projectSlugInput").fill("fog-letter")
                page.locator("#projectGenreInput").fill("都市悬疑")
                page.locator("#projectChapterTitleInput").fill("第一章 门缝")
                page.locator("#projectPremiseInput").fill("一个人收到无法追踪的信后重新检查记忆。")
                page.locator("#projectStyleInput").fill("克制，少解释心理，多用动作和物件承压。")
                opening = "我站在门口，心里很乱，想起过去很多事情。"
                page.locator("#projectOpeningInput").fill(opening)
                page.locator("#submitProjectBtn").click()
                user_project = workspace / "fog-letter"
                expect(page.get_by_text("章节列表")).to_be_visible(timeout=5000)
                expect(page.get_by_test_id("empty-workspace")).to_be_hidden(timeout=5000)
                expect(page.locator("#chapterRows").get_by_text("chapters/ch01.md")).to_be_visible()
                assert "还没有书稿项目" not in page.locator("#dashboardMain").inner_text(), "created project must replace the empty workspace state"
                assert page.evaluate("() => window.BookWorkbench.state.project?.summary?.relativePath") == "fog-letter", "created project should open automatically"
                assert (user_project / ".bookai" / "discussions.jsonl").exists(), "new projects must include discussion sidecar"
                assert (user_project / ".codex" / "skills" / "revise-with-annotations" / "SKILL.md").exists(), "new projects must include project-local Codex skills"
                codex_skills = page.evaluate("""async () => await window.BookWorkbench.api('/api/codex/skills', {method:'POST', body: JSON.stringify({})})""")
                assert codex_skills["response"]["data"][0]["skills"][0]["scope"] == "repo", "Codex skills endpoint must stay project/repo scoped"
                page.screenshot(path=str(artifacts / "02-created-project-opened.png"), full_page=True)

                page.evaluate("() => { window.BookWorkbench.state.project = null; window.BookWorkbench.setView('dashboard'); }")
                expect(page.get_by_test_id("project-list-panel")).to_be_visible(timeout=5000)
                expect(page.get_by_test_id("empty-workspace")).to_be_hidden(timeout=5000)
                expect(page.get_by_test_id("project-card").filter(has_text="雾中来信")).to_be_visible(timeout=5000)
                page.get_by_test_id("project-card").filter(has_text="雾中来信").click()
                expect(page.get_by_text("章节列表")).to_be_visible(timeout=5000)
                expect(page.locator("#chapterRows").get_by_text("chapters/ch01.md")).to_be_visible()

                page.locator("#chapterRows tr").first.click()
                expect(page.locator("#chapterSelect")).to_contain_text("第一章 门缝", timeout=5000)
                page.screenshot(path=str(artifacts / "03-open-created-project.png"), full_page=True)

                before_discussion = (user_project / "chapters" / "ch01.md").read_text(encoding="utf-8")
                page.locator('[data-view="discussions"]').click()
                expect(page.locator("#view-discussions")).to_be_visible(timeout=5000)
                discussion_text = "讨论：这一章要先保留悬疑，不要解释情绪，用信封和停顿表现压力。"
                page.locator("#discussionTextInput").fill(discussion_text)
                page.locator("#submitDiscussionBtn").click()
                expect(page.get_by_test_id("discussion-card").filter(has_text="DS-001")).to_be_visible(timeout=5000)
                assert discussion_text in (user_project / ".bookai" / "discussions.jsonl").read_text(encoding="utf-8")
                assert (user_project / "chapters" / "ch01.md").read_text(encoding="utf-8") == before_discussion, "discussion must not mutate chapter"
                page.screenshot(path=str(artifacts / "04-discussion-sidecar.png"), full_page=True)

                page.locator('[data-view="editor"]').click()
                expect(page.locator("#docView")).to_contain_text(opening, timeout=5000)
                before_annotation = (user_project / "chapters" / "ch01.md").read_text(encoding="utf-8")
                page.evaluate("""
                () => {
                    const el = document.querySelector('.paragraph[data-block="ch01-p001"] .ptext');
                    const text = el.firstChild;
                    const start = text.textContent.indexOf('心里很乱');
                    const end = start + '心里很乱，想起过去很多事情。'.length;
                    const range = document.createRange();
                    range.setStart(text, start);
                    range.setEnd(text, end);
                    const selection = window.getSelection();
                    selection.removeAllRanges();
                    selection.addRange(range);
                }
                """)
                page.locator('.paragraph[data-block="ch01-p001"] .ptext').click(button="right")
                expect(page.locator("#selectionMenu")).to_be_visible(timeout=5000)
                expect(page.locator("#selectionPreview")).to_contain_text("心里很乱，想起过去很多事情。", timeout=5000)
                page.locator("#selectionAddAnnotationBtn").click()
                expect(page.get_by_test_id("annotation-modal")).to_be_visible()
                expect(page.locator("#annotationSelectedInput")).to_have_value("心里很乱，想起过去很多事情。")
                page.locator("#annotationBodyInput").fill("这里太像 AI 了，不要解释内心，用动作表现。")
                page.locator("#submitAnnotationBtn").click()
                expect(page.locator("#annotationPanel").get_by_text("AN-001")).to_be_visible(timeout=5000)
                assert "这里太像 AI" in (user_project / ".bookai" / "annotations.jsonl").read_text(encoding="utf-8")
                assert (user_project / "chapters" / "ch01.md").read_text(encoding="utf-8") == before_annotation, "annotation sidecar flow must not mutate chapter body"
                page.screenshot(path=str(artifacts / "05-annotation-sidecar.png"), full_page=True)

                page.locator("#reviseCurrentBtn").click()
                expect(page.locator("#view-diff")).to_be_visible(timeout=5000)
                expect(page.locator("#patchValidity")).to_contain_text("通过", timeout=5000)
                assert (user_project / "chapters" / "ch01.md").read_text(encoding="utf-8") == before_annotation, "AI revise before accept must not mutate chapter"
                page.screenshot(path=str(artifacts / "06-user-book-diff-before-accept.png"), full_page=True)

                before_commits = git_count(user_project) if (user_project / ".git").exists() else 0
                page.locator("#acceptPatchBtn").click()
                expect(page.locator("#view-editor")).to_be_visible(timeout=5000)
                final_text = (user_project / "chapters" / "ch01.md").read_text(encoding="utf-8")
                after_commits = git_count(user_project)
                assert "没有署名的空白处" in final_text, "accepted patch must apply codex-generated main path revision"
                assert opening not in final_text, "accepted patch should replace the annotated direct-emotion wording"
                assert after_commits == before_commits + 1, "accepted patch must create git commit"
                page.screenshot(path=str(artifacts / "07-user-book-after-accept-commit.png"), full_page=True)

                # Fixture flow remains covered for the original AN-041 safety/runtime path.
                write_black_rain_fixture(workspace / "black-rain-after", init_git=True)
                page.goto(base_url, wait_until="networkidle")
                expect(page.get_by_test_id("project-card").filter(has_text="黑雨之后")).to_be_visible(timeout=5000)
                page.evaluate("() => window.BookWorkbench.openProject('black-rain-after')")
                page.wait_for_function("() => window.BookWorkbench.state?.project?.summary?.slug === 'black-rain-after' && document.querySelector('#chapterRows')?.textContent.includes('chapters/ch05.md')")
                page.locator("#chapterRows").get_by_text("chapters/ch05.md").click()
                expect(page.locator("#chapterSelect")).to_contain_text("第五章", timeout=5000)
                before_fixture = (workspace / "black-rain-after" / "chapters" / "ch05.md").read_text(encoding="utf-8")
                page.evaluate("""
                () => {
                    const el = document.querySelector('.paragraph[data-block="ch05-p018"] .ptext');
                    const text = el.firstChild;
                    const selected = '我的心里很复杂，我想起了过去的种种，内心充满了矛盾和挣扎。';
                    const start = text.textContent.indexOf(selected);
                    const range = document.createRange();
                    range.setStart(text, start);
                    range.setEnd(text, start + selected.length);
                    const selection = window.getSelection();
                    selection.removeAllRanges();
                    selection.addRange(range);
                }
                """)
                page.evaluate("() => window.BookWorkbench.updateSelectionMenu()")
                expect(page.locator("#selectionMenu")).to_be_visible(timeout=5000)
                page.locator('.paragraph[data-block="ch05-p018"] .ptext').dblclick()
                expect(page.get_by_test_id("annotation-modal")).to_be_visible(timeout=5000)
                expect(page.locator("#annotationSelectedInput")).to_have_value("我的心里很复杂，我想起了过去的种种，内心充满了矛盾和挣扎。")
                page.locator("#annotationBodyInput").fill("这里太像 AI 了，不要解释内心，用动作表现。")
                page.locator("#submitAnnotationBtn").click()
                expect(page.locator("#annotationPanel").get_by_text("AN-1000")).to_be_visible(timeout=5000)
                assert (workspace / "black-rain-after" / "chapters" / "ch05.md").read_text(encoding="utf-8") == before_fixture
                page.locator("#reviseCurrentBtn").click()
                expect(page.locator("#view-diff")).to_be_visible(timeout=5000)
                expect(page.locator("#patchValidity")).to_contain_text("通过", timeout=5000)
                page.screenshot(path=str(artifacts / "08-fixture-diff-before-accept.png"), full_page=True)

                # Regression checks for the previously no-op UI controls from fresh QA.
                page.locator('[data-view="rules"]').click()
                expect(page.locator("#view-rules")).to_be_visible(timeout=5000)
                page.locator("#ruleFilterBtn").click()
                expect(page.locator("#ruleFilterPanel")).to_be_visible(timeout=5000)
                page.locator('[data-rule-filter="style"]').first.click()
                expect(page.locator("#ruleFilterSummary")).to_contain_text("风格", timeout=5000)
                page.locator('[data-view="editor"]').click()
                page.locator('[data-annotation-tab="suggestions"]').click()
                expect(page.locator("#annotationPanel")).to_contain_text("安全边界", timeout=5000)
                page.locator('[data-view="diff"]').click()
                page.locator("#toggleDiffReasonBtn").click()
                page.wait_for_function("() => document.querySelector('#diffReasonCard')?.classList.contains('collapsed')")
                page.locator("#toggleDiffReasonBtn").click()
                page.wait_for_function("() => !document.querySelector('#diffReasonCard')?.classList.contains('collapsed')")

                fixture_before_commits = git_count(workspace / "black-rain-after")
                page.locator("#acceptPatchBtn").click()
                expect(page.locator("#view-editor")).to_be_visible(timeout=5000)
                fixture_final = (workspace / "black-rain-after" / "chapters" / "ch05.md").read_text(encoding="utf-8")
                fixture_after_commits = git_count(workspace / "black-rain-after")
                assert "纸杯沿一点点捏扁" in fixture_final, "accepted fixture patch must apply expected AN-041 text"
                assert fixture_after_commits == fixture_before_commits + 1, "accepted fixture patch must create git commit"
                page.screenshot(path=str(artifacts / "09-fixture-after-accept-commit.png"), full_page=True)

                flow_report.update(
                    {
                        "ok": True,
                        "workspace": str(workspace),
                        "userProject": {
                            "discussionSidecar": str(user_project / ".bookai" / "discussions.jsonl"),
                            "annotationSidecar": str(user_project / ".bookai" / "annotations.jsonl"),
                            "commitCountBefore": before_commits,
                            "commitCountAfter": after_commits,
                        },
                        "fixtureProject": {
                            "commitCountBefore": fixture_before_commits,
                            "commitCountAfter": fixture_after_commits,
                        },
                    }
                )
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    bad_console = [m for m in console_messages if m["type"] in {"error"}]
    (artifacts / "console.json").write_text(json.dumps(console_messages, ensure_ascii=False, indent=2), encoding="utf-8")
    (artifacts / "page-errors.json").write_text(json.dumps(page_errors, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "ok": not bad_console and not page_errors and bool(flow_report.get("ok")),
        "consoleErrors": bad_console,
        "pageErrors": page_errors,
        "artifacts": str(artifacts),
        "flow": flow_report,
    }
    (artifacts / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
