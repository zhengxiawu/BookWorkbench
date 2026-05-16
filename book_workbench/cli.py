"""Command-line entry points for the BookWorkbench Runtime MVP."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .patch_engine import apply_patch, load_patch, make_annotation_patch, preview_diff, validate_patch
from .project import load_project


def _print_validation(result) -> int:
    if result.issues:
        for issue in result.issues:
            print(f"{issue.severity.upper()} {issue.code}: {issue.message}", file=sys.stderr)
    print("valid" if result.valid else "invalid")
    return 0 if result.valid else 1


def cmd_inspect(args: argparse.Namespace) -> int:
    context = load_project(args.project)
    payload = {
        "root": str(context.root),
        "rules": [rule.id for rule in context.rules],
        "chapterStatus": context.chapter_status,
        "annotations": [annotation.id for annotation in context.annotations],
        "blocks": {file: sorted(blocks) for file, blocks in context.blocks.items()},
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_generate_sample_patch(args: argparse.Namespace) -> int:
    context = load_project(args.project)
    patch = make_annotation_patch(context, args.annotation)
    print(json.dumps(patch, ensure_ascii=False, indent=2))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    context = load_project(args.project)
    patch = load_patch(args.patch)
    return _print_validation(validate_patch(context, patch, allow_reviewed=args.allow_reviewed))


def cmd_diff(args: argparse.Namespace) -> int:
    context = load_project(args.project)
    patch = load_patch(args.patch)
    result = validate_patch(context, patch, allow_reviewed=args.allow_reviewed)
    if not result.valid:
        return _print_validation(result)
    print(preview_diff(context, patch), end="")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    context = load_project(args.project)
    patch = load_patch(args.patch)
    result = apply_patch(context, patch, allow_reviewed=args.allow_reviewed)
    return _print_validation(result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BookWorkbench Manuscript Runtime MVP")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser("inspect", help="Load a project and print indexed runtime context")
    inspect.add_argument("--project", required=True)
    inspect.set_defaults(func=cmd_inspect)

    sample = sub.add_parser("generate-sample-patch", help="Generate a deterministic PatchProposal for a sample annotation")
    sample.add_argument("--project", required=True)
    sample.add_argument("--annotation", required=True)
    sample.set_defaults(func=cmd_generate_sample_patch)

    validate = sub.add_parser("validate", help="Validate a PatchProposal")
    validate.add_argument("--project", required=True)
    validate.add_argument("--patch", required=True)
    validate.add_argument("--allow-reviewed", action="store_true")
    validate.set_defaults(func=cmd_validate)

    diff = sub.add_parser("diff", help="Preview a validated PatchProposal as unified diff")
    diff.add_argument("--project", required=True)
    diff.add_argument("--patch", required=True)
    diff.add_argument("--allow-reviewed", action="store_true")
    diff.set_defaults(func=cmd_diff)

    apply_cmd = sub.add_parser("apply", help="Apply a validated PatchProposal")
    apply_cmd.add_argument("--project", required=True)
    apply_cmd.add_argument("--patch", required=True)
    apply_cmd.add_argument("--allow-reviewed", action="store_true")
    apply_cmd.set_defaults(func=cmd_apply)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
