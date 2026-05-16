"""Skill discovery and precedence resolution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional

SCOPE_PRECEDENCE = ["project", "user", "builtin", "codex", "remote"]
RESERVED_SAFETY_SKILLS = {"safe-patch-apply", "chapter-lock-policy", "git-checkpoint-policy"}


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    path: Path
    scope: str

    def to_dict(self) -> Dict[str, str]:
        payload = asdict(self)
        payload["path"] = self.path.as_posix()
        return payload


class SkillLoadError(RuntimeError):
    pass


def _parse_frontmatter(text: str) -> Dict[str, str]:
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    raw = parts[1]
    result: Dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        result[key.strip()] = value
    return result


def load_skill(path: str | Path, *, scope: str) -> SkillDefinition:
    skill_path = Path(path)
    if skill_path.is_dir():
        skill_path = skill_path / "SKILL.md"
    if not skill_path.exists():
        raise SkillLoadError(f"Skill file does not exist: {skill_path}")
    frontmatter = _parse_frontmatter(skill_path.read_text(encoding="utf-8"))
    name = frontmatter.get("name") or skill_path.parent.name
    description = frontmatter.get("description", "")
    return SkillDefinition(name=name, description=description, path=skill_path.resolve(), scope=scope)


def discover_skills(roots: Mapping[str, str | Path | None]) -> List[SkillDefinition]:
    discovered: List[SkillDefinition] = []
    for scope in SCOPE_PRECEDENCE:
        root = roots.get(scope)
        if not root:
            continue
        root_path = Path(root)
        if not root_path.exists():
            continue
        for skill_file in sorted(root_path.glob("*/SKILL.md")):
            discovered.append(load_skill(skill_file, scope=scope))
    return discovered


def _skill_precedence(skill: SkillDefinition) -> tuple[int, int]:
    """Return deterministic precedence for a discovered skill.

    Normal skills follow product precedence: project > user > builtin > codex > remote.
    Reserved safety skills are different: a bundled builtin safety policy must not
    be shadowed by project/user content. If no builtin safety skill exists, fall
    back to normal scope precedence so discovery remains deterministic.
    """

    if skill.name in RESERVED_SAFETY_SKILLS and skill.scope == "builtin":
        return (-1, SCOPE_PRECEDENCE.index(skill.scope))
    return (0, SCOPE_PRECEDENCE.index(skill.scope))


def resolve_skills(skills: Iterable[SkillDefinition]) -> Dict[str, SkillDefinition]:
    resolved: Dict[str, SkillDefinition] = {}
    for skill in skills:
        existing = resolved.get(skill.name)
        if existing is None or _skill_precedence(skill) < _skill_precedence(existing):
            resolved[skill.name] = skill
    return resolved


def build_skill_roots(
    *,
    project_root: str | Path,
    builtin_root: str | Path | None = None,
    user_root: str | Path | None = None,
    codex_root: str | Path | None = None,
) -> Dict[str, str | Path | None]:
    project_root = Path(project_root)
    return {
        "project": project_root / ".agents" / "skills",
        "user": user_root,
        "builtin": builtin_root,
        "codex": codex_root,
        "remote": None,
    }
