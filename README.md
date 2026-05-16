# BookWorkbench

BookWorkbench is a Manuscript Runtime MVP built from the included `manuscript_runtime_codex_appserver_v2` design package.

The product direction is a Markdown-first long-form writing workbench: users read manuscript chapters, add annotations, ask AI skills for structured proposals, review diffs, and only then let the Runtime safely apply and commit changes.

## Repository layout

- `manuscript_runtime_codex_appserver_v2/` — original design package extracted from the archive.
  - `docs/` — product, module, app-server, annotation, skill, and safety specs.
  - `schemas/` — JSON Schemas for `AnnotationIR`, `PatchProposal`, and skill events.
  - `skills/` — example built-in skill prompts.
  - `sample_project/` — small Markdown manuscript fixture.
  - `screenshots/` — generated UI direction images.
- `book_workbench/` — no-dependency Python Runtime MVP.
- `tests/` — regression tests for project loading and patch safety.

## Runtime MVP capabilities

The current implementation focuses on the safety-critical path described by the design package:

1. Load a manuscript project.
2. Read `book.spec.md`, `style-guide.md`, `rules.yaml`, `.bookai/chapter-status.yaml`, annotations, and Markdown block anchors.
3. Validate `PatchProposal` objects against Runtime safety policy.
4. Preview unified diffs without editing manuscripts.
5. Apply only validated patches while preserving `mw:block` anchors.

It intentionally does **not** call an external model or Codex app-server yet. In the intended architecture, Codex app-server produces structured `PatchProposal` JSON; this Runtime validates, previews, applies, and commits it.

## Quick start

```bash
python3 -m book_workbench.cli inspect \
  --project manuscript_runtime_codex_appserver_v2/sample_project

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

To apply a patch, run the same command against a copy or a working branch:

```bash
python3 -m book_workbench.cli apply \
  --project manuscript_runtime_codex_appserver_v2/sample_project \
  --patch /tmp/an-041.patch.json
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## GitHub remote

The intended remote is:

```bash
git@github.com:zhengxiawu/BookWorkbench.git
```

If SSH authentication is configured locally, push with:

```bash
git push -u origin main
```
