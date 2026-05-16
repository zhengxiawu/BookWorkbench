# AGENTS.md

You are working inside a manuscript runtime project. The product is a writing workbench where Markdown manuscripts, annotations, writing rules, AI patch proposals, and Git history are managed by the Manuscript Runtime.

## Hard rules

1. Do not directly rewrite manuscript files unless the Runtime explicitly asks you to apply a validated patch.
2. Prefer producing structured proposals: `ProjectPlan`, `AnnotationIR`, `RuleProposal`, or `PatchProposal`.
3. Never modify chapters whose status is `locked`.
4. For chapters whose status is `reviewed`, propose changes only unless secondary approval is provided.
5. Every writing change must cite a source annotation or an explicit user instruction.
6. Do not edit `.bookai` metadata by hand unless a Runtime tool requires it.
7. Preserve `mw:block` anchors unless the patch engine regenerates them.
8. Summarize affected files, annotations used, rules used, and excluded chapters before proposing changes.
9. All accepted changes should be committed through the Git service.
10. Preserve author voice. Avoid generic AI prose.

## Project concepts

- `AnnotationIR`: normalized annotations from Markdown, DOCX, PDF, or WPS-saved documents.
- `Rule`: long-term writing preference extracted from author notes.
- `PatchProposal`: an AI-generated proposed change that must pass Runtime validation.
- `ChapterStatus`: one of `draft`, `unreviewed`, `reviewed`, `locked`.

## Default workflow

1. Read `book.spec.md`, `style-guide.md`, `rules.yaml`, `.bookai/chapter-status.yaml`, and relevant annotations.
2. Classify annotations.
3. Propose local changes and/or long-term rules.
4. Generate PatchProposal JSON.
5. Wait for Runtime/user approval.
