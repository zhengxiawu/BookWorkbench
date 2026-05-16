# BookWorkbench

BookWorkbench is a Markdown-first Manuscript Runtime MVP built from the included `manuscript_runtime_codex_appserver_v2` design package.

The product direction is a long-form writing workbench: users read manuscript chapters, add annotations, ask AI skills for structured proposals, review diffs, and only then let the Runtime safely apply and commit changes.

## Repository layout

- `manuscript_runtime_codex_appserver_v2/` — original design package extracted from the archive.
  - `docs/` — product, module, app-server, annotation, skill, and safety specs.
  - `schemas/` — JSON Schemas for `AnnotationIR`, `PatchProposal`, and skill events.
  - `skills/` — example built-in skill prompts.
  - `sample_project/` — small Markdown manuscript fixture.
  - `screenshots/` — generated UI direction images.
- `book_workbench/` — no-dependency Python Runtime MVP.
- `tests/` — regression tests for project loading, safety policy, runtime runs, CLI smoke, and Git seams.

## Runtime MVP capabilities

The current implementation focuses on the safety-critical loop described by the design package:

1. Load a manuscript project.
2. Read `book.spec.md`, `style-guide.md`, `rules.yaml`, `.bookai/chapter-status.yaml`, annotations, skills, and Markdown block anchors.
3. Query/classify annotations and compute applicable rules.
4. Discover built-in/project/user skills with deterministic precedence.
5. Run deterministic Runtime skill flows:
   - `revise-with-annotations` → `PatchProposal`
   - `extract-writing-rules` → `RuleProposal`
   - `propagate-rules` → chapter-grouped patch proposals, excluding reviewed/locked chapters
6. Validate `PatchProposal` objects against Runtime safety policy.
7. Preview unified diffs without editing manuscripts.
8. Apply only validated patches while preserving `mw:block` anchors.
9. Write JSONL audit events for runtime runs and patch preview/apply/reject.
10. Open a local browser app that exercises the same Runtime safety boundary.
11. Check a local Codex `app-server` process with a real bounded `initialize` handshake.

It intentionally does **not** send manuscript edits through a model yet. In the intended architecture, Codex app-server produces structured proposal JSON; this Runtime validates, previews, applies, audits, and commits it. The current app-server seam verifies that Codex app-server can be launched and initialized locally.

## Quick start

Inspect a project:

```bash
python3 -m book_workbench.cli inspect \
  --project manuscript_runtime_codex_appserver_v2/sample_project \
  --skills-root manuscript_runtime_codex_appserver_v2/skills
```

List annotations, rules, and skills:

```bash
python3 -m book_workbench.cli annotations \
  --project manuscript_runtime_codex_appserver_v2/sample_project

python3 -m book_workbench.cli rules \
  --project manuscript_runtime_codex_appserver_v2/sample_project \
  --file chapters/ch05.md

python3 -m book_workbench.cli skills \
  --project manuscript_runtime_codex_appserver_v2/sample_project \
  --skills-root manuscript_runtime_codex_appserver_v2/skills
```

Run a deterministic Runtime skill without modifying files:

```bash
python3 -m book_workbench.cli run-skill \
  --project manuscript_runtime_codex_appserver_v2/sample_project \
  --skills-root manuscript_runtime_codex_appserver_v2/skills \
  --skill revise-with-annotations \
  --annotation AN-041
```

Open the local app against a temporary copy:

```bash
tmpdir="$(mktemp -d)"
cp -R manuscript_runtime_codex_appserver_v2/sample_project "$tmpdir/sample_project"
python3 -m book_workbench.cli serve \
  --project "$tmpdir/sample_project" \
  --skills-root manuscript_runtime_codex_appserver_v2/skills \
  --port 8765 \
  --open
```

Then use the browser buttons to:

1. run `revise-with-annotations`
2. preview the unified diff
3. apply the validated patch
4. inspect the audit trail

The same operations are available through JSON endpoints:

- `GET /api/health` — local app, Runtime, and Codex app-server initialize health
- `GET /api/project`
- `GET /api/chapters/<url-encoded chapters/path.md>`
- `GET /api/annotations`
- `POST /api/skills/run`
- `POST /api/patch/preview`
- `POST /api/patch/apply`
- `GET /api/audit`

State-changing `POST` endpoints require the per-server token printed by
`serve` as `X-BookWorkbench-Token` (or `Authorization: Bearer <token>`). Keep
`--host` on loopback unless you intentionally accept local-network risk.

Check Codex app-server health directly:

```bash
python3 -m book_workbench.cli codex-health --timeout 5
```

Generate, validate, and preview a sample patch:

```bash
python3 -m book_workbench.cli generate-sample-patch \
  --project manuscript_runtime_codex_appserver_v2/sample_project \
  --annotation AN-041 > /tmp/an-041.patch.json

python3 -m book_workbench.cli validate \
  --project manuscript_runtime_codex_appserver_v2/sample_project \
  --patch /tmp/an-041.patch.json

python3 -m book_workbench.cli diff \
  --project manuscript_runtime_codex_appserver_v2/sample_project \
  --patch /tmp/an-041.patch.json
```

Apply only against a copy or working branch:

```bash
tmpdir="$(mktemp -d)"
cp -R manuscript_runtime_codex_appserver_v2/sample_project "$tmpdir/sample_project"
python3 -m book_workbench.cli apply \
  --project "$tmpdir/sample_project" \
  --patch /tmp/an-041.patch.json
python3 -m book_workbench.cli audit --project "$tmpdir/sample_project"
```

## Safety policy implemented

Validation rejects:

- missing required proposal/change fields
- wrong-typed proposal/change fields
- missing or unknown source annotations, except explicit `USER-*` instruction sources
- non-`chapters/*.md` targets, path traversal, and symlink/out-of-project chapter targets
- locked chapters
- reviewed chapters unless `--allow-reviewed` is used and each change sets `requiresSecondaryApproval: true`
- unknown files or block IDs
- `beforeHash` mismatch
- duplicate/conflicting changes to the same block
- `afterText` containing `mw:block` anchors
- non-empty `afterText` on `delete_block`

## Tests

```bash
python3 -m compileall -q book_workbench tests
python3 -m unittest discover -s tests -v
```

Current suite covers project loading, annotation/rule/skill/runtime flows, patch validation, malformed proposal rejection, symlink target rejection, diff/apply operations, CLI smoke, audit rejection, and Git wrapper no-op behavior.

## GitHub remote

The intended remote is:

```bash
git@github.com:zhengxiawu/BookWorkbench.git
```

If SSH authentication is configured locally, push with:

```bash
git push -u origin main
```
