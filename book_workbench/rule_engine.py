"""Rule querying and deterministic rule proposal helpers."""

from __future__ import annotations

from dataclasses import asdict
from typing import Dict, Iterable, List, Optional

from .annotation_engine import classify_annotation
from .models import Annotation, ProjectContext, Rule


def rule_to_dict(rule: Rule) -> Dict[str, object]:
    return asdict(rule)


def applicable_rules(context: ProjectContext, file_path: str) -> List[Rule]:
    status = context.status_for_file(file_path)
    return [
        rule
        for rule in context.rules
        if rule.status == "active"
        and (not rule.apply_to or status in rule.apply_to)
        and status not in rule.exclude
    ]


def next_rule_id(context: ProjectContext) -> str:
    max_id = 0
    for rule in context.rules:
        if rule.id.startswith("R-") and rule.id[2:].isdigit():
            max_id = max(max_id, int(rule.id[2:]))
    return f"R-{max_id + 1:03d}"


def propose_rules_from_annotations(
    context: ProjectContext,
    annotations: Optional[Iterable[Annotation]] = None,
) -> Dict[str, object]:
    selected = list(annotations if annotations is not None else context.annotations)
    durable = [item for item in selected if classify_annotation(item) in {"style", "tone", "rhythm", "structure"}]
    if not durable:
        return {
            "id": "RP-empty",
            "summary": "没有足够可沉淀为长期规则的批注。",
            "rules": [],
            "confidence": 0.0,
        }

    source_ids = [item.id for item in durable]
    primary_type = classify_annotation(durable[0])
    if primary_type == "style":
        text = "人物心理优先通过动作、停顿、回避和场景压力体现，避免直接解释。"
    elif primary_type == "rhythm":
        text = "节奏批注应优先通过动作密度、停顿和场景细节调节，而不是用总结性说明加速跳转。"
    else:
        text = durable[0].text

    return {
        "id": f"RP-{source_ids[0]}",
        "summary": "从作者批注中提炼可复用写作规则。",
        "rules": [
            {
                "idSuggestion": next_rule_id(context),
                "type": primary_type,
                "text": text,
                "source_annotations": source_ids,
                "apply_to": ["draft", "unreviewed"],
                "exclude": ["reviewed", "locked"],
                "priority": "high" if any(item.priority == "high" for item in durable) else "medium",
                "confidence": 0.86 if len(durable) == 1 else 0.92,
                "examples": [item.text for item in durable[:3]],
            }
        ],
    }
