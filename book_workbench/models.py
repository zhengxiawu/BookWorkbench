"""Typed data structures for the BookWorkbench runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class MarkdownBlock:
    id: str
    file: str
    anchor: str
    before_hash: str
    text: str
    start_line: int
    end_line: int
    anchor_line: int


@dataclass(frozen=True)
class Annotation:
    id: str
    file: str
    block_id: str
    text: str
    annotation_type: str
    priority: str
    status: str
    before_hash: Optional[str] = None
    selected_text: Optional[str] = None


@dataclass(frozen=True)
class Rule:
    id: str
    type: str
    text: str
    source_annotations: List[str] = field(default_factory=list)
    priority: str = "medium"
    apply_to: List[str] = field(default_factory=list)
    exclude: List[str] = field(default_factory=list)
    status: str = "active"


@dataclass(frozen=True)
class ChapterStatus:
    file: str
    status: str


@dataclass(frozen=True)
class ProjectContext:
    root: Path
    book_spec: str
    style_guide: str
    rules: List[Rule]
    chapter_status: Dict[str, str]
    annotations: List[Annotation]
    blocks: Dict[str, Dict[str, MarkdownBlock]]

    def status_for_file(self, file_path: str) -> str:
        status = self.chapter_status.get(file_path, "draft")
        return {
            "annotated": "unreviewed",
            "briefed": "unreviewed",
            "revised": "unreviewed",
        }.get(status, status)

    def block(self, file_path: str, block_id: str) -> MarkdownBlock:
        return self.blocks[file_path][block_id]


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    issues: List[ValidationIssue]
    touches_locked: bool = False
    touches_reviewed: bool = False

    def error_messages(self) -> List[str]:
        return [issue.message for issue in self.issues if issue.severity == "error"]


PatchProposal = Dict[str, Any]
