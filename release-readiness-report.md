# Release Readiness Report — BookWorkbench

Date: 2026-05-16

## Result

Status: **PASS for the implemented MVP safety/UI gates**.

The app now starts in workspace mode with no preloaded novel. Users see an empty project list, can open the “新建项目” modal, create a project, see it appear in the list, and enter the manuscript workbench. Runtime safety gates keep manuscript writes behind PatchProposal validation, Diff review, and Git checkpointing.

## Verification summary

| Area | Result | Evidence |
| --- | --- | --- |
| Python compile | PASS | `python3 -m compileall -q book_workbench tests scripts` |
| Unit/integration tests | PASS | `python3 -m unittest discover -s tests -v` — 52 tests |
| Browser E2E | PASS | `python3 scripts/browser_e2e.py` |
| JS syntax | PASS | extracted served script checked by `node --check` inside app-server tests |
| Diff hygiene | PASS | `git diff --check` |
| Codex app-server health | PASS | `python3 -m book_workbench.cli codex-health --timeout 3` returned `ok: true` |
| CLI workspace create | PASS | `create-project` with empty opening text creates a blank first chapter |

Browser artifacts are saved under `.omx/evidence/browser-e2e/`:

- `01-empty-workspace.png`
- `02-created-project-listed.png`
- `03-open-created-project.png`
- `04-diff-preview-before-accept.png`
- `05-after-accept-commit.png`
- `console.json` — `[]`
- `page-errors.json` — `[]`

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
| TC-012 browser UI flow: empty workspace → create/open project → annotate → Diff → accept → commit | PASS |

## Fixes applied during QA

- Removed hard-coded demo book UI state from default app startup.
- Added workspace discovery/list/open support before any Runtime project is loaded.
- Added a visible new-project modal instead of one-click demo creation.
- Added sidecar annotation creation endpoint with block id, selected text, offsets, beforeHash, and block-index update.
- Added Runtime acceptance-time Git checkpointing.
- Added source annotation selectedText/beforeHash drift validation to prevent silent wrong-block edits.
- Added suspicious malicious annotation handling that refuses automatic manuscript changes.
- Added app-server file-change approval policy seam that declines direct manuscript/metadata/locked/reviewed writes outside Runtime PatchProposal flow.
- Added browser E2E harness and 12-gate release tests.

## Known residual risks

- DOCX/PDF import/export roundtrip, large-project stress, crash recovery, and real model Skill Evals are not implemented in this MVP pass.
- Codex app-server is currently health-checked and policy-modeled; full real thread/turn stream integration remains a future layer.
- Browser E2E is Playwright-based deterministic automation, not a live Computer Use Agent nightly harness.

## Safety policy version

MVP Runtime policy in `book_workbench.patch_engine` + `book_workbench.runtime` as of this report.
