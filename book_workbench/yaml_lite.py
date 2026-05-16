"""Small YAML readers for the repository's constrained sample files.

This intentionally avoids adding PyYAML for the MVP. It supports the exact
subset used by ``rules.yaml`` and ``.bookai/chapter-status.yaml``: mappings,
lists of mappings, scalar strings, and inline string arrays.
"""

from __future__ import annotations

from typing import Any, Dict, List


def _strip_comment(line: str) -> str:
    in_quote = False
    quote = ""
    out = []
    for char in line:
        if char in {"'", '"'}:
            if not in_quote:
                in_quote = True
                quote = char
            elif quote == char:
                in_quote = False
        if char == "#" and not in_quote:
            break
        out.append(char)
    return "".join(out).rstrip()


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if value == "":
        return {}
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [parse_scalar(part.strip()) for part in inner.split(",")]
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value


def load_chapter_status(text: str) -> Dict[str, str]:
    statuses: Dict[str, str] = {}
    current_file = None
    in_chapters = False

    for raw in text.splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0 and stripped == "chapters:":
            in_chapters = True
            continue
        if not in_chapters:
            continue
        if indent == 2 and stripped.endswith(":"):
            current_file = stripped[:-1]
            continue
        if indent >= 4 and current_file and stripped.startswith("status:"):
            statuses[current_file] = str(parse_scalar(stripped.split(":", 1)[1]))

    return statuses


def load_rules(text: str) -> List[Dict[str, Any]]:
    rules: List[Dict[str, Any]] = []
    current: Dict[str, Any] | None = None
    in_rules = False

    for raw in text.splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0 and stripped == "rules:":
            in_rules = True
            continue
        if not in_rules:
            continue
        if indent == 2 and stripped.startswith("- "):
            if current:
                rules.append(current)
            current = {}
            remainder = stripped[2:].strip()
            if remainder:
                key, value = remainder.split(":", 1)
                current[key.strip()] = parse_scalar(value)
            continue
        if current is not None and indent >= 4 and ":" in stripped:
            key, value = stripped.split(":", 1)
            current[key.strip()] = parse_scalar(value)

    if current:
        rules.append(current)
    return rules
