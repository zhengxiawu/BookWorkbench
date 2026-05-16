"""Annotation querying and lightweight classification."""

from __future__ import annotations

from dataclasses import asdict
from typing import Dict, Iterable, List, Optional

from .models import Annotation, ProjectContext


CLASSIFICATION_KEYWORDS = {
    "style": ("AI", "心理", "动作", "风格", "公众号", "解释内心"),
    "rhythm": ("节奏", "太快", "太慢", "拉长", "缩短"),
    "continuity": ("冲突", "设定", "前文", "第", "矛盾"),
    "fact": ("事实", "资料", "错误", "考证"),
    "tone": ("语气", "口吻", "冷", "克制"),
    "structure": ("结构", "章节", "顺序", "铺垫"),
}


def annotation_to_dict(annotation: Annotation) -> Dict[str, object]:
    return asdict(annotation)


def list_annotations(
    context: ProjectContext,
    *,
    file_path: Optional[str] = None,
    status: Optional[str] = None,
    annotation_type: Optional[str] = None,
    include_resolved: bool = True,
) -> List[Annotation]:
    annotations = context.annotations
    if file_path is not None:
        annotations = [item for item in annotations if item.file == file_path]
    if status is not None:
        annotations = [item for item in annotations if item.status == status]
    if annotation_type is not None:
        annotations = [item for item in annotations if classify_annotation(item) == annotation_type]
    if not include_resolved:
        annotations = [item for item in annotations if item.status not in {"resolved", "ignored", "accepted"}]
    return annotations


def open_annotations(context: ProjectContext, *, file_path: Optional[str] = None) -> List[Annotation]:
    return list_annotations(context, file_path=file_path, status="open", include_resolved=False)


def classify_annotation(annotation: Annotation) -> str:
    if annotation.annotation_type and annotation.annotation_type != "other":
        return annotation.annotation_type
    text = annotation.text
    for category, keywords in CLASSIFICATION_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return category
    return "other"


def classification_summary(annotations: Iterable[Annotation]) -> Dict[str, int]:
    summary: Dict[str, int] = {}
    for annotation in annotations:
        category = classify_annotation(annotation)
        summary[category] = summary.get(category, 0) + 1
    return summary
