"""Command-line entry points for the BookWorkbench Runtime MVP."""

from __future__ import annotations

import argparse
import json
import sys

from .annotation_engine import annotation_to_dict, classification_summary, list_annotations
from .app_server import serve
from .audit import AuditLog
from .codex_client import CodexAppServerClient
from .codex_skill_eval import run_codex_skill_evals
from .patch_engine import load_patch, make_annotation_patch, validate_patch as validate_patch_proposal
from .project import load_project
from .project_creator import create_book_project
from .rule_engine import applicable_rules, propose_rules_from_annotations, rule_to_dict
from .runtime import RuntimeOrchestrator
from .skill_manager import build_skill_roots, discover_skills, resolve_skills


def print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_inspect(args: argparse.Namespace) -> int:
    runtime = RuntimeOrchestrator(args.project, builtin_skills_root=args.skills_root)
    print_json(runtime.inspect())
    return 0


def cmd_annotations(args: argparse.Namespace) -> int:
    context = load_project(args.project)
    annotations = list_annotations(
        context,
        file_path=args.file,
        status=args.status,
        annotation_type=args.type,
        include_resolved=args.include_resolved,
    )
    print_json(
        {
            "annotations": [annotation_to_dict(item) for item in annotations],
            "summary": classification_summary(annotations),
        }
    )
    return 0


def cmd_rules(args: argparse.Namespace) -> int:
    context = load_project(args.project)
    if args.propose:
        print_json(propose_rules_from_annotations(context))
    elif args.file:
        print_json({"rules": [rule_to_dict(rule) for rule in applicable_rules(context, args.file)]})
    else:
        print_json({"rules": [rule_to_dict(rule) for rule in context.rules]})
    return 0


def cmd_skills(args: argparse.Namespace) -> int:
    context = load_project(args.project)
    roots = build_skill_roots(project_root=context.root, builtin_root=args.skills_root)
    skills = resolve_skills(discover_skills(roots))
    print_json({"skills": {name: skill.to_dict() for name, skill in sorted(skills.items())}})
    return 0


def cmd_run_skill(args: argparse.Namespace) -> int:
    runtime = RuntimeOrchestrator(
        args.project,
        builtin_skills_root=args.skills_root,
        write_audit=not args.no_audit,
    )
    result = runtime.run_skill(args.skill, annotation_ids=args.annotation, scope_file=args.file)
    print_json(result)
    return 0


def cmd_generate_sample_patch(args: argparse.Namespace) -> int:
    context = load_project(args.project)
    patch = make_annotation_patch(context, args.annotation)
    print_json(patch)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    runtime = RuntimeOrchestrator(args.project, write_audit=not args.no_audit)
    patch = load_patch(args.patch)
    result = runtime.validate_patch(patch, allow_reviewed=args.allow_reviewed)
    if result["issues"]:
        for issue in result["issues"]:
            print(f"{issue['severity'].upper()} {issue['code']}: {issue['message']}", file=sys.stderr)
    print("valid" if result["valid"] else "invalid")
    return 0 if result["valid"] else 1


def cmd_diff(args: argparse.Namespace) -> int:
    runtime = RuntimeOrchestrator(args.project, write_audit=not args.no_audit)
    patch = load_patch(args.patch)
    result = runtime.preview_patch(patch, allow_reviewed=args.allow_reviewed)
    if not result["validation"]["valid"]:
        for issue in result["validation"]["issues"]:
            print(f"{issue['severity'].upper()} {issue['code']}: {issue['message']}", file=sys.stderr)
        print("invalid")
        return 1
    print(result["diff"], end="")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    runtime = RuntimeOrchestrator(args.project, write_audit=not args.no_audit)
    patch = load_patch(args.patch)
    result = runtime.accept_patch(patch, allow_reviewed=args.allow_reviewed)
    if result["validation"]["issues"]:
        for issue in result["validation"]["issues"]:
            print(f"{issue['severity'].upper()} {issue['code']}: {issue['message']}", file=sys.stderr)
    print("valid" if result["validation"]["valid"] else "invalid")
    return 0 if result["validation"]["valid"] else 1


def cmd_audit(args: argparse.Namespace) -> int:
    print_json({"events": AuditLog(args.project).read()})
    return 0


def cmd_codex_health(args: argparse.Namespace) -> int:
    command = args.command if args.command else ["codex", "app-server"]
    print_json(CodexAppServerClient(command=command, timeout_seconds=args.timeout, cwd=args.cwd).health())
    return 0


def cmd_codex_skills(args: argparse.Namespace) -> int:
    command = args.command if args.command else ["codex", "app-server"]
    scan_cwds = args.cwd or []
    process_cwd = scan_cwds[0] if scan_cwds else None
    result = CodexAppServerClient(command=command, timeout_seconds=args.timeout, cwd=process_cwd).list_skills(
        cwds=scan_cwds,
        force_reload=not args.no_force_reload,
    )
    print_json(result)
    return 0 if result.get("ok") else 1


def cmd_codex_probe(args: argparse.Namespace) -> int:
    command = args.command if args.command else ["codex", "app-server"]
    prompt = args.prompt or 'Return exactly JSON: {"ok": true, "source": "codex-app-server"}'
    result = CodexAppServerClient(command=command, timeout_seconds=args.timeout, cwd=args.cwd).run_probe_turn(
        prompt=prompt,
        cwd=args.cwd,
    )
    print_json(result)
    return 0 if result.get("ok") else 1


def cmd_codex_patch_probe(args: argparse.Namespace) -> int:
    command = args.command if args.command else ["codex", "app-server"]
    prompt = args.prompt or (
        'Return exactly this JSON object and no markdown: '
        '{"id":"PP-probe","summary":"probe only","sourceAnnotations":["USER-codex-probe"],"rulesUsed":[],"changes":[]}'
    )

    def validate(proposal: object) -> dict:
        if not isinstance(proposal, dict):
            return {"valid": False, "issues": [{"code": "invalid_patch", "message": "not an object"}]}
        if not args.project:
            required = {"id", "summary", "sourceAnnotations", "rulesUsed", "changes"}
            missing = sorted(required - set(proposal))
            return {
                "valid": not missing,
                "issues": [{"code": "missing_field", "message": f"missing {field}"} for field in missing],
                "shapeOnly": True,
            }
        result = validate_patch_proposal(load_project(args.project), proposal)
        return {"valid": result.valid, "issues": [issue.__dict__ for issue in result.issues]}

    result = CodexAppServerClient(command=command, timeout_seconds=args.timeout, cwd=args.cwd or args.project).run_patch_proposal_turn(
        prompt=prompt,
        cwd=args.cwd or args.project,
        patch_validator=validate,
    )
    print_json(result)
    return 0 if result.get("ok") else 1


def cmd_codex_skill_eval(args: argparse.Namespace) -> int:
    command = args.command if args.command else ["codex", "app-server"]
    result = run_codex_skill_evals(
        project=args.project,
        output_dir=args.output,
        command=command,
        timeout_seconds=args.timeout,
        eval_ids=args.eval or None,
    )
    print_json(result)
    return 0 if result.get("ok") else 1


def cmd_serve(args: argparse.Namespace) -> int:
    if args.project is None and args.workspace is None:
        parser_workspace_error = "serve requires --workspace for empty workspace mode or --project for direct project mode"
        raise ValueError(parser_workspace_error)
    serve(
        args.project,
        workspace_root=args.workspace,
        builtin_skills_root=args.skills_root,
        host=args.host,
        port=args.port,
        open_browser=args.open,
    )
    return 0


def cmd_create_project(args: argparse.Namespace) -> int:
    print_json(
        create_book_project(
            args.workspace,
            title=args.title,
            slug=args.slug,
            genre=args.genre,
            premise=args.premise,
            style=args.style,
            chapter_title=args.chapter_title,
            opening_text=args.opening_text,
        )
    )
    return 0


def _add_project_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BookWorkbench Manuscript Runtime MVP")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser("inspect", help="Load a project and print indexed runtime context")
    _add_project_arg(inspect)
    inspect.add_argument("--skills-root")
    inspect.set_defaults(func=cmd_inspect)

    annotations = sub.add_parser("annotations", help="List/filter AnnotationIR-like sidecar annotations")
    _add_project_arg(annotations)
    annotations.add_argument("--file")
    annotations.add_argument("--status")
    annotations.add_argument("--type")
    annotations.add_argument("--include-resolved", action="store_true")
    annotations.set_defaults(func=cmd_annotations)

    rules = sub.add_parser("rules", help="List applicable rules or propose new durable rules")
    _add_project_arg(rules)
    rules.add_argument("--file")
    rules.add_argument("--propose", action="store_true")
    rules.set_defaults(func=cmd_rules)

    skills = sub.add_parser("skills", help="Discover available skills")
    _add_project_arg(skills)
    skills.add_argument("--skills-root")
    skills.set_defaults(func=cmd_skills)

    run_skill = sub.add_parser("run-skill", help="Run a deterministic Runtime skill and return structured output")
    _add_project_arg(run_skill)
    run_skill.add_argument("--skills-root")
    run_skill.add_argument("--skill", required=True)
    run_skill.add_argument("--annotation", action="append")
    run_skill.add_argument("--file")
    run_skill.add_argument("--no-audit", action="store_true")
    run_skill.set_defaults(func=cmd_run_skill)

    sample = sub.add_parser("generate-sample-patch", help="Generate a deterministic PatchProposal for a sample annotation")
    _add_project_arg(sample)
    sample.add_argument("--annotation", required=True)
    sample.set_defaults(func=cmd_generate_sample_patch)

    validate = sub.add_parser("validate", help="Validate a PatchProposal")
    _add_project_arg(validate)
    validate.add_argument("--patch", required=True)
    validate.add_argument("--allow-reviewed", action="store_true")
    validate.add_argument("--no-audit", action="store_true")
    validate.set_defaults(func=cmd_validate)

    diff = sub.add_parser("diff", help="Preview a validated PatchProposal as unified diff")
    _add_project_arg(diff)
    diff.add_argument("--patch", required=True)
    diff.add_argument("--allow-reviewed", action="store_true")
    diff.add_argument("--no-audit", action="store_true")
    diff.set_defaults(func=cmd_diff)

    apply_cmd = sub.add_parser("apply", help="Apply a validated PatchProposal")
    _add_project_arg(apply_cmd)
    apply_cmd.add_argument("--patch", required=True)
    apply_cmd.add_argument("--allow-reviewed", action="store_true")
    apply_cmd.add_argument("--no-audit", action="store_true")
    apply_cmd.set_defaults(func=cmd_apply)

    audit = sub.add_parser("audit", help="Read the project audit log")
    _add_project_arg(audit)
    audit.set_defaults(func=cmd_audit)

    codex_health = sub.add_parser("codex-health", help="Check local Codex app-server initialize health")
    codex_health.add_argument("--timeout", type=float, default=5.0)
    codex_health.add_argument("--cwd")
    codex_health.add_argument(
        "--command",
        nargs="+",
        help="Override command, default: codex app-server",
    )
    codex_health.set_defaults(func=cmd_codex_health)

    codex_skills = sub.add_parser("codex-skills", help="List real Codex app-server skills for explicit cwd scopes")
    codex_skills.add_argument("--timeout", type=float, default=5.0)
    codex_skills.add_argument("--cwd", action="append", default=[], help="CWD whose project-local .codex/skills should be scanned; repeatable")
    codex_skills.add_argument("--no-force-reload", action="store_true")
    codex_skills.add_argument(
        "--command",
        nargs=argparse.REMAINDER,
        help="Override command, default: codex app-server",
    )
    codex_skills.set_defaults(func=cmd_codex_skills)

    codex_probe = sub.add_parser("codex-probe", help="Run a real read-only Codex app-server thread/turn probe")
    codex_probe.add_argument("--timeout", type=float, default=15.0)
    codex_probe.add_argument("--cwd", default=".")
    codex_probe.add_argument("--prompt")
    codex_probe.add_argument(
        "--command",
        nargs=argparse.REMAINDER,
        help="Override command, default: codex app-server",
    )
    codex_probe.set_defaults(func=cmd_codex_probe)

    codex_patch_probe = sub.add_parser("codex-patch-probe", help="Run a real read-only Codex turn and validate PatchProposal JSON output")
    codex_patch_probe.add_argument("--timeout", type=float, default=15.0)
    codex_patch_probe.add_argument("--cwd")
    codex_patch_probe.add_argument("--project", help="Optional project used for Runtime PatchProposal validation")
    codex_patch_probe.add_argument("--prompt")
    codex_patch_probe.add_argument(
        "--command",
        nargs=argparse.REMAINDER,
        help="Override command, default: codex app-server",
    )
    codex_patch_probe.set_defaults(func=cmd_codex_patch_probe)

    codex_skill_eval = sub.add_parser("codex-skill-eval", help="Run real Codex app-server Skill evals with Runtime validation")
    codex_skill_eval.add_argument("--project", required=True)
    codex_skill_eval.add_argument("--output", required=True, help="Directory for eval artifacts")
    codex_skill_eval.add_argument("--timeout", type=float, default=60.0)
    codex_skill_eval.add_argument("--eval", action="append", help="Eval id to run; repeatable. Defaults to core suite")
    codex_skill_eval.add_argument(
        "--command",
        nargs=argparse.REMAINDER,
        help="Override command, default: codex app-server",
    )
    codex_skill_eval.set_defaults(func=cmd_codex_skill_eval)

    serve_cmd = sub.add_parser("serve", help="Start the local browser app")
    serve_cmd.add_argument("--project", help="Open this project immediately; omit for empty workspace mode")
    serve_cmd.add_argument("--workspace", help="Workspace root for listing/creating projects; required when --project is omitted")
    serve_cmd.add_argument("--skills-root")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8765)
    serve_cmd.add_argument("--open", action="store_true", help="Open the app URL in the default browser")
    serve_cmd.set_defaults(func=cmd_serve)

    create_project = sub.add_parser("create-project", help="Create a new local manuscript project")
    create_project.add_argument("--workspace", required=True)
    create_project.add_argument("--title", required=True)
    create_project.add_argument("--slug")
    create_project.add_argument("--genre", default="")
    create_project.add_argument("--premise", default="")
    create_project.add_argument("--style", default="")
    create_project.add_argument("--chapter-title", default="第一章")
    create_project.add_argument("--opening-text", default="")
    create_project.set_defaults(func=cmd_create_project)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
