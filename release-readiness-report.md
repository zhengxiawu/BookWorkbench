# Release Readiness Report — BookWorkbench

Date: 2026-05-17

## Result

Status: **PASS for the implemented MVP safety/UI gates**.

The app now starts in workspace mode with no preloaded novel. Users see an empty project list, can open the “新建项目” modal, create a project, land directly in the created project dashboard, record project discussions, add sidecar annotations, generate a PatchProposal, review the Diff, accept it, and get a Git checkpoint. The main “AI 处理” action now tries the real Codex app-server/project-local Skill path first, accepts only Runtime-valid PatchProposal output scoped to the selected annotation, and falls back to deterministic Runtime skills when Codex is unavailable or unsafe. Runtime safety gates keep manuscript writes behind PatchProposal validation, Diff review, and Git checkpointing.

## Verification summary

| Area | Result | Evidence |
| --- | --- | --- |
| Python compile | PASS | `python3 -m compileall -q book_workbench tests scripts` |
| Unit/integration tests | PASS | `python3 -m unittest discover -s tests -v` — 69 tests |
| Browser E2E | PASS | `python3 scripts/browser_e2e.py` |
| JS syntax | PASS | extracted served script checked by `node --check` inside app-server tests |
| Diff hygiene | PASS | `git diff --check` |
| Codex app-server health | PASS | `python3 -m book_workbench.cli codex-health --timeout 3` returned `ok: true` |
| Real Codex app-server turn stream | PASS | `python3 -m book_workbench.cli codex-probe --timeout 60 --cwd .` observed `thread/started`, `turn/started`, `item/agentMessage/delta`, `item/completed`, `turn/completed` |
| Real Codex PatchProposal probe | PASS | `python3 -m book_workbench.cli codex-patch-probe --timeout 30 --cwd .` returned JSON proposal and shape validator result; project-scoped app endpoint uses Runtime validation |
| Real Codex Skill Eval suite | PASS | `python3 -m book_workbench.cli codex-skill-eval --project <fixture> --output .omx/evidence/codex-skill-eval-2026-05-17 --timeout 90` — 13/13 evals passed |
| Real Codex main AI path | PASS | `.omx/evidence/codex-main-path-2026-05-17/summary.json` — `/api/ai/revise` used Codex app-server output, kept chapter unchanged before accept, then preview/apply wrote through Runtime and Git checkpoint |
| Project-local Codex skills | PASS | New projects scaffold `.codex/skills/*/SKILL.md`; `codex skills/list` sees them as `scope: repo`; tests assert no global `~/.codex/skills` writes |
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

Codex app-server evidence is saved under `.omx/evidence/codex-appserver-2026-05-16/`:

- `codex-health.json` — initialize health
- `codex-probe.json` — real read-only thread/turn stream
- `codex-patch-probe.json` — real read-only PatchProposal JSON parse + shape validation
- `codex-skills-project.json` — project `.codex/skills` loaded as Codex `repo` scope
- `summary.json` — `ok: true`

Codex Skill Eval evidence is saved under `.omx/evidence/codex-skill-eval-2026-05-17/`:

- `summary.json` — real app-server Skill eval suite, 13/13 passed
- `skills-list.json` — project `.codex/skills` visible as `repo` scope
- `eval-revise_with_annotations_basic.json` — model generated Runtime-valid PatchProposal for `AN-041`
- `eval-malicious_annotation_injection.json` — malicious annotation produced no accepted manuscript changes
- `eval-locked_chapter_denial.json` — locked chapter request produced no accepted locked changes
- `eval-reviewed_chapter_secondary_approval.json` — reviewed chapter was not silently accepted
- `eval-revise_hash_drift.json` — stale annotation anchor/hash required remap and produced no accepted manuscript edit
- `eval-revise_out_of_scope_valid_patch.json` — Runtime-valid but wrong-scope patch was caught by selected-annotation scope guard
- `eval-propagate_rules_basic.json` and `eval-propagate_rules_excludes_locked_reviewed.json` — rule propagation proposed valid draft/unreviewed patches and excluded locked/reviewed chapters
- `eval-extract_writing_rules_basic.json` and `eval-extract_writing_rules_malicious_annotation.json` — rule extraction/dedup and malicious annotation handling passed
- `eval-skill_scope_precedence.json` — BookWorkbench skills loaded project-locally without global install
- `eval-revise_malformed_output.json` — malformed/invalid model output was rejected
- `eval-codex_timeout_or_tool_failure_fallback.json` — simulated Codex failure fell back to deterministic Runtime output without pre-accept mutation

Real Codex main-path evidence is saved under `.omx/evidence/codex-main-path-2026-05-17/`:

- `summary.json` — `/api/ai/revise` source `codex-app-server`, pre-accept unchanged, preview valid, apply successful, Git commit count increased 1 → 2
- `patch.json` — model-generated PatchProposal used by the guarded main path

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
7. Ran AI revise through the guarded Codex-first main path; verified Diff/PatchProposal appeared and chapter text was still unchanged before acceptance.
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
- Connected the normal UI “AI 处理” action to a Codex-first `/api/ai/revise` path with Runtime validation, selected-annotation scope checks, and deterministic fallback.
- Expanded the real Codex Skill Eval runner and CLI (`codex-skill-eval`) to a 13-case regression matrix covering revise, malicious input, locked/reviewed protection, hash drift, scope guard, propagation, rule extraction, project-local skill scope, malformed output, and fallback.
- Added browser E2E harness and 12-gate release tests.
- Fixed post-create UX so the new project opens immediately instead of leaving the “还没有书稿项目” empty state above the card.
- Added a regression assertion that created projects hide `empty-workspace` and set `state.project.summary.relativePath`.

## Computer-use status

A true OpenAI Computer Use action tool is **not exposed in this local Codex tool environment**, so this run used Playwright browser actions as the safe local UI harness. The E2E script records screenshots and validates backend state/files/Git commits; it does not rely on screenshot-only self-reporting.

## Codex app-server and Skill scope update

- Earlier QA was correct: before the Codex integration passes, BookWorkbench only health-checked `initialize` and `/api/skills/run` used deterministic Runtime skills.
- The app-server verification path now covers: `initialize` → `thread/start` → `turn/start` → stream event capture → `turn/completed`. Probe/eval turns are read-only/ephemeral and do not write manuscript files.
- The normal UI “AI 处理” path now calls `/api/ai/revise`, which builds a tightly scoped `revise-with-annotations` prompt, invokes the real Codex app-server, parses PatchProposal JSON, runs Runtime validation plus selected-annotation scope checks, and only then sends it to Diff review. If Codex output is invalid, empty, unsafe, or unavailable, the path falls back to deterministic Runtime skill output.
- `codex-skill-eval` runs real model-backed Skill evals against project-local `.codex/skills` and records stream events, validation results, parsed JSON artifacts, and before/after file snapshots. The current default suite covers 13 cases across revise, propagation, extraction, malicious input, locked/reviewed chapters, stale hashes, wrong-scope proposals, malformed output, skill scope, and fallback.
- App endpoints `/api/codex/skills`, `/api/codex/probe`, `/api/codex/patch-probe`, and `/api/ai/revise` are project-scoped and route approval requests through Runtime policy.
- New BookWorkbench projects get only project-local skills under `.codex/skills/`; the app does not create or modify `~/.codex/skills`. Runtime discovery now uses the same `.codex/skills` project scope as Codex app-server.

## Known residual risks

- DOCX/PDF import/export roundtrip, large-project stress, and crash recovery are not implemented in this MVP pass.
- The real Codex Skill Eval suite now covers the core project-local skills and safety boundaries, but it is still not a substitute for DOCX/PDF import, crash recovery, or large-project stress coverage.
- True Computer Use Agent nightly harness remains blocked until the CUA action tool is available in the execution environment.

## Safety policy version

MVP Runtime policy in `book_workbench.patch_engine` + `book_workbench.runtime` as of this report.
