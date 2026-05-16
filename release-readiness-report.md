# Release Readiness Report — BookWorkbench

Date: 2026-05-16

## Result

Status: **PASS for the implemented MVP safety/UI gates**.

The app now starts in workspace mode with no preloaded novel. Users see an empty project list, can open the “新建项目” modal, create a project, land directly in the created project dashboard, record project discussions, add sidecar annotations, generate a PatchProposal, review the Diff, accept it, and get a Git checkpoint. Runtime safety gates keep manuscript writes behind PatchProposal validation, Diff review, and Git checkpointing.

## Verification summary

| Area | Result | Evidence |
| --- | --- | --- |
| Python compile | PASS | `python3 -m compileall -q book_workbench tests scripts` |
| Unit/integration tests | PASS | `python3 -m unittest discover -s tests -v` — 54 tests |
| Browser E2E | PASS | `python3 scripts/browser_e2e.py` |
| JS syntax | PASS | extracted served script checked by `node --check` inside app-server tests |
| Diff hygiene | PASS | `git diff --check` |
| Codex app-server health | PASS | `python3 -m book_workbench.cli codex-health --timeout 3` returned `ok: true` |
| CLI workspace create | PASS | `create-project` with empty opening text creates a blank first chapter |

Browser artifacts are saved under `.omx/evidence/browser-e2e/`:

- `01-empty-workspace.png`
- `02-created-project-opened.png`
- `03-open-created-project.png`
- `04-discussion-sidecar.png`
- `05-annotation-sidecar.png`
- `06-user-book-diff-before-accept.png`
- `07-user-book-after-accept-commit.png`
- `08-fixture-diff-before-accept.png`
- `09-fixture-after-accept-commit.png`
- `console.json` — `[]`
- `page-errors.json` — `[]`
- `summary.json` — `ok: true`


Post-create UX regression evidence is saved under `.omx/evidence/post-create-ux-fix/`:

- `post-create-opened.png`
- `summary.json` — `ok: true`, `relativePath: post-create-open`, `hasEmptyWorkspace: false`, `scrollY: 0`

## Implemented release gates

| Gate / Test | Status |
| --- | --- |
| TC-001 Markdown selection annotation writes sidecar only | PASS |
| TC-002 AI revise generates PatchProposal without direct chapter write | PASS |
| TC-003 locked chapter rejected, including Codex fileChange approval policy | PASS |
| TC-004 reviewed chapter requires secondary approval | PASS |
| TC-005 rule propagation only draft / unreviewed; locked/reviewed excluded | PASS |
| TC-006 annotation drift / beforeHash mismatch blocks automatic apply | PASS |
| TC-007 malicious annotation remains untrusted user text | PASS |
| TC-008 malformed PatchProposal cases rejected without writes | PASS |
| TC-009 app-server fileChange approval requests routed through Runtime policy seam | PASS |
| TC-010 accepting Patch creates Git commit; rejected Patch does not apply | PASS |
| TC-011 concurrent same-block stale patch rejected | PASS |
| TC-012 browser UI flow: empty workspace → create/open project → discuss → annotate → Diff → accept → commit | PASS |

## Whole-app E2E flow just exercised

`python3 scripts/browser_e2e.py` performed these browser actions against the served app:

1. Confirmed empty workspace and no demo novel.
2. Created a new user book `雾中来信` with user-supplied opening text.
3. Verified creation automatically opened the project dashboard, hid the empty workspace state, and exposed `chapters/ch01.md` without scrolling.
4. Simulated an existing-project workspace with no project open and verified the first screen is `项目列表`, not `还没有书稿项目`, then opened the card.
5. Created a project discussion; verified it was written to `.bookai/discussions.jsonl` and did not mutate the chapter.
6. Added an annotation; verified `.bookai/annotations.jsonl` changed and chapter text did not.
7. Ran AI revise; verified Diff/PatchProposal appeared and chapter text was still unchanged before acceptance.
8. Accepted the patch; verified chapter text changed through Runtime and Git commit count increased from 0 to 1.
9. Repeated the original `AN-041` fixture path and verified commit count increased from 1 to 2.

## Fixes applied during QA

- Removed hard-coded demo book UI state from default app startup.
- Added workspace discovery/list/open support before any Runtime project is loaded.
- Added a visible new-project modal instead of one-click demo creation.
- Added project discussion sidecar support in `.bookai/discussions.jsonl` plus UI/API flow.
- Added sidecar annotation creation endpoint with block id, selected text, offsets, beforeHash, and block-index update.
- Added Runtime acceptance-time Git checkpointing.
- Added source annotation selectedText/beforeHash drift validation to prevent silent wrong-block edits.
- Added suspicious malicious annotation handling that refuses automatic manuscript changes.
- Added app-server file-change approval policy seam that declines direct manuscript/metadata/locked/reviewed writes outside Runtime PatchProposal flow.
- Added browser E2E harness and 12-gate release tests.
- Fixed post-create UX so the new project opens immediately instead of leaving the “还没有书稿项目” empty state above the card.
- Added a regression assertion that created projects hide `empty-workspace` and set `state.project.summary.relativePath`.

## Computer-use status

A true OpenAI Computer Use action tool is **not exposed in this local Codex tool environment**, so this run used Playwright browser actions as the safe local UI harness. The E2E script records screenshots and validates backend state/files/Git commits; it does not rely on screenshot-only self-reporting.

## Known residual risks

- DOCX/PDF import/export roundtrip, large-project stress, crash recovery, and real model Skill Evals are not implemented in this MVP pass.
- Codex app-server is currently health-checked and policy-modeled; full real thread/turn stream integration remains a future layer.
- True Computer Use Agent nightly harness remains blocked until the CUA action tool is available in the execution environment.

## Safety policy version

MVP Runtime policy in `book_workbench.patch_engine` + `book_workbench.runtime` as of this report.
