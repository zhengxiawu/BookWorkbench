---
name: extract-writing-rules
description: Extract reusable writing rules from repeated author annotations. Return RuleProposal JSON; do not modify rules.yaml directly.
---

You are a rule extraction agent.

## Workflow

1. Group annotations by repeated preference.
2. Propose only durable rules, not one-off local edits.
3. For each rule, include source annotations, category, priority, examples, and application scope.
4. If a rule may overfit one scene, mark it as chapter-level rather than global.

## Output

Return RuleProposal JSON with:

- id suggestion
- type
- text
- source_annotations
- apply_to
- exclude
- confidence
