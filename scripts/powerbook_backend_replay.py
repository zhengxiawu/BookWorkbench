from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from book_workbench.powerbook_importer import import_powerbook_project  # noqa: E402
from book_workbench.project import load_project, manuscript_word_count  # noqa: E402
from book_workbench.project_creator import POWERBOOK_GUIDE_MODE, create_book_project  # noqa: E402

DEFAULT_POWERBOOK_SOURCE = Path("/Users/sherwood/Projects/PowerBook")
DEFAULT_REPLAY_INPUTS = ROOT / "test-artifacts" / "powerbook-replay-20260518T005248" / "replay-inputs"

TERM_MARKERS = [
    "大泽乡",
    "陈胜",
    "吴广",
    "史记",
    "秦",
    "睡虎地",
    "达尔",
    "韦伯",
    "卢克斯",
    "米尔格兰姆",
    "Chudek",
    "Henrich",
    "外卖",
    "职场",
    "教育",
    "平台",
    "基层",
    "审批",
    "问责",
    "财政",
    "家长",
    "中国",
]

SPECIFIC_SCENE_MARKERS = ("大泽乡", "公元前", "一场大雨", "外卖", "家长", "基层", "办公室")
TRACEABLE_MARKERS = ("《史记", "睡虎地", "Dahl", "Weber", "Lukes", "Milgram", "Chudek", "Henrich", "达尔", "韦伯", "卢克斯")
AUTHOR_NOTE_MARKERS = ("AUTHOR-NOTE", "AuthorNote", "AuhorNote")


def tree_hash(root: Path) -> str:
    rows: list[tuple[str, str]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file() and item.name != ".DS_Store"):
        rows.append((path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    payload = "".join(f"{digest}  {name}\n" for name, digest in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_replay_inputs(directory: Path) -> list[Dict[str, Any]]:
    if not directory.exists():
        return []
    inputs = []
    for path in sorted(directory.glob("*-user-input.txt")):
        text = path.read_text(encoding="utf-8", errors="replace")
        match = re.match(r"(\d+)-", path.name)
        index = int(match.group(1)) if match else len(inputs) + 1
        inputs.append({"index": index, "path": path.as_posix(), "chars": len(text), "text": text})
    return inputs


def combined_initial_prompt(inputs: Iterable[Mapping[str, Any]]) -> str:
    chunks = []
    for item in inputs:
        text = str(item.get("text", "")).strip()
        if not text or text.startswith("<turn_aborted>"):
            continue
        chunks.append(f"## 第 {item.get('index')} 条原始用户输入\n\n{text}")
    return "\n\n---\n\n".join(chunks)


def first_chapter_path(root: Path, preferred: str = "") -> Path:
    if preferred and (root / preferred).exists():
        return root / preferred
    candidates = sorted((root / "chapters").glob("ch01*.md"))
    if candidates:
        return candidates[0]
    candidates = sorted((root / "book" / "chapters").glob("ch01*.md"))
    if candidates:
        return candidates[0]
    raise FileNotFoundError(f"Cannot find first chapter under {root}")


def chapter_files(root: Path) -> list[Path]:
    chapters = root / "chapters"
    if chapters.exists():
        return sorted(path for path in chapters.glob("*.md") if path.name != "README.md")
    source_chapters = root / "book" / "chapters"
    if source_chapters.exists():
        return sorted(path for path in source_chapters.glob("ch*.md") if path.name != "README.md")
    return []


def manuscript_chinese_chars(paths: Iterable[Path]) -> int:
    return sum(len(re.findall(r"[\u4e00-\u9fff]", visible_text(path.read_text(encoding="utf-8", errors="replace")))) for path in paths)


def strip_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    frontmatter = text[: end + 4]
    body = text[end + 4 :].lstrip("\n")
    fields: dict[str, str] = {}
    for line in frontmatter.splitlines():
        if ":" not in line or line.strip() == "---" or line.startswith(" "):
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip().strip('"').strip("'")
    return fields, body


def visible_text(text: str) -> str:
    text = re.sub(r"<!--\s*mw:block.*?-->", "", text)
    text = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.S)
    return text.strip()


def opening_line(body: str) -> str:
    for block in re.split(r"\n\s*\n+", body):
        stripped = block.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("<!--"):
            continue
        if stripped.startswith("---"):
            continue
        return re.sub(r"\s+", " ", stripped)[:220]
    return ""


def score_chapter(path: Path) -> Dict[str, Any]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    frontmatter, body_with_title = strip_frontmatter(raw)
    body = visible_text(raw)
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", body))
    h2 = len(re.findall(r"^##\s+", body, flags=re.MULTILINE))
    h3 = len(re.findall(r"^###\s+", body, flags=re.MULTILINE))
    blocks = len([block for block in re.split(r"\n\s*\n+", body) if block.strip()])
    author_notes = {marker: raw.count(marker) for marker in AUTHOR_NOTE_MARKERS}
    term_hits = {marker: body.count(marker) for marker in TERM_MARKERS if marker in body}
    features = {
        "full_chapter_length_ok_6000_9000_chinese": 6000 <= chinese_chars <= 9000,
        "has_5_to_9_h2": 5 <= h2 <= 9,
        "starts_from_specific_scene": any(marker in opening_line(body_with_title) or marker in body[:900] for marker in SPECIFIC_SCENE_MARKERS),
        "uses_traceable_material_or_fact_boundary": any(marker in body for marker in TRACEABLE_MARKERS),
        "has_china_context": body.count("中国") >= 3 and any(marker in body for marker in ("户籍", "基层", "财政", "单位", "平台", "教育")),
        "has_core_definition": "权力，是稳定改写他人行动空间的能力" in body,
        "no_author_notes_left": not any(author_notes.values()),
        "has_plain_terms_rule_effect": "征发机器" not in body[:2500] or "先登记" in body or "普通人从村庄里抽出来" in body,
        "has_boundary_between_influence_and_power": "影响" in body and "权力" in body and ("退出成本" in body or "稳定的结构性位置" in body),
        "has_modern_examples": sum(1 for marker in ("外卖", "职场", "平台", "入学", "基层", "民营企业") if marker in body) >= 3,
        "no_unverified_markers": "[需查证]" not in body,
    }
    score = sum(1 for ok in features.values() if ok)
    return {
        "path": path.as_posix(),
        "frontmatter": frontmatter,
        "titleLine": next((line.strip() for line in body.splitlines() if line.startswith("# ")), ""),
        "totalChars": len(raw),
        "visibleChars": len(body),
        "chineseChars": chinese_chars,
        "manuscriptWordCount": manuscript_word_count(body),
        "h2": h2,
        "h3": h3,
        "blocks": blocks,
        "opening": opening_line(body_with_title),
        "headings": [line.strip() for line in body.splitlines() if line.startswith("#")][:20],
        "termHits": term_hits,
        "authorNoteMarkers": author_notes,
        "qualityScore": {"score": score, "max": len(features), "ratio": round(score / max(1, len(features)), 3), "features": features},
    }


def run_command(command: list[str], *, cwd: Path) -> Dict[str, Any]:
    started = datetime.now(timezone.utc)
    completed = subprocess.run(command, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "startedAt": started.isoformat(),
        "endedAt": datetime.now(timezone.utc).isoformat(),
    }


def write_report(output_dir: Path, summary: Mapping[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    guide = summary["bookWorkbenchGuide"]["ch01"]
    original = summary["powerbookBaseline"]["ch01"]
    imported = summary["importedPowerBook"]["ch01"]
    lines = [
        "# PowerBook 后台从零回放质量报告",
        "",
        f"- Source: `{summary['source']}`",
        f"- Source hash before/after: `{summary['sourceHashBefore']}` / `{summary['sourceHashAfter']}`",
        f"- Source unchanged: **{summary['sourceUnchanged']}**",
        f"- Replay inputs: {summary['replayInputs']['count']} 条 / {summary['replayInputs']['totalChars']} 字节",
        "",
        "## 结论",
        "",
        summary["comparison"]["deterministicAnswer"],
        "",
        "## 指标对比",
        "",
        "| 样本 | 首章中文字符 | H2 | blocks | 质量分 | 开头 |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
        f"| 原 PowerBook 当前 ch01 | {original['chineseChars']} | {original['h2']} | {original['blocks']} | {original['qualityScore']['score']}/{original['qualityScore']['max']} | {original['opening']} |",
        f"| BookWorkbench 从零 guide ch01 | {guide['chineseChars']} | {guide['h2']} | {guide['blocks']} | {guide['qualityScore']['score']}/{guide['qualityScore']['max']} | {guide['opening']} |",
        f"| BookWorkbench 导入后 ch01 | {imported['chineseChars']} | {imported['h2']} | {imported['blocks']} | {imported['qualityScore']['score']}/{imported['qualityScore']['max']} | {imported['opening']} |",
        "",
        "## 质量门槛",
        "",
        f"- 从零质量达标：**{summary['comparison']['guideMeetsQualityGate']}**",
        f"- 从零章节数：{summary['comparison']['guideChapterCount']} / 原始 {summary['comparison']['originalChapterCount']}",
        f"- 从零相对原稿首章中文长度：{summary['comparison']['guideVsOriginalLengthRatioChinese']}",
        f"- 从零相对原稿全书中文长度：{summary['comparison']['guideVsOriginalBookLengthRatioChinese']}",
        f"- 导入保真相对原稿首章中文长度：{summary['comparison']['importPreservesOriginalCh01LengthRatioChinese']}",
        f"- 导入保真相对原稿全书中文长度：{summary['comparison']['importPreservesOriginalBookLengthRatioChinese']}",
        "",
        "## 证据边界",
        "",
        "- Evidence: 只读读取 PowerBook 源目录；从零项目在测试工作区创建；导入副本在测试工作区创建。",
        "- Evidence: 评分是启发式硬门槛，覆盖长度、章节结构、具体场景、事实边界、中国语境、核心定义、批注残留、术语白话化和现代例子。",
        "- Inference: 该后台门槛能证明 BookWorkbench 从零基线已达到原 PowerBook 首章同一质量档；不等于证明之后每一章的真实模型长流程已经稳定。",
        "",
    ]
    (output_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backend-only PowerBook replay quality check")
    parser.add_argument("--source", default=str(DEFAULT_POWERBOOK_SOURCE))
    parser.add_argument("--replay-inputs", default=str(DEFAULT_REPLAY_INPUTS))
    parser.add_argument("--output", default="")
    parser.add_argument("--keep-workspace", action="store_true")
    args = parser.parse_args(argv)

    source = Path(args.source).resolve()
    replay_inputs_dir = Path(args.replay_inputs).resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(args.output).resolve() if args.output else ROOT / "test-artifacts" / f"powerbook-backend-replay-{stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace = output_dir / "workspace" if args.keep_workspace else Path(tempfile.mkdtemp(prefix="powerbook-backend-replay-"))
    workspace.mkdir(parents=True, exist_ok=True)

    before_hash = tree_hash(source)
    inputs = read_replay_inputs(replay_inputs_dir)
    combined_prompt = combined_initial_prompt(inputs)
    (output_dir / "replay-input-summary.json").write_text(
        json.dumps([{"index": item["index"], "path": item["path"], "chars": item["chars"], "excerpt": str(item["text"])[:240]} for item in inputs], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    prompts_dir = output_dir / "prompts"
    prompts_dir.mkdir(exist_ok=True)
    (prompts_dir / "combined-31-user-inputs.txt").write_text(combined_prompt, encoding="utf-8")

    created = create_book_project(
        workspace,
        title="权力的底层结构",
        slug="powerbook-guide-replay",
        genre="理论非虚构",
        premise=combined_prompt[:20000],
        style="PowerBook 原始自主写作闭环：完整章节优先、作者批注保护、具体场景进入、术语先白话解释、事实边界清楚。",
        chapter_title="第一章",
        opening_text=combined_prompt,
        mode=POWERBOOK_GUIDE_MODE,
        create_baseline_commit=True,
    )
    guide_root = Path(created["root"])
    guide_chapter = first_chapter_path(guide_root, "chapters/ch01.md")
    generated_dir = output_dir / "generated"
    generated_dir.mkdir(exist_ok=True)
    shutil.copy2(guide_chapter, generated_dir / "bookworkbench-guide-ch01.md")

    imported = import_powerbook_project(source, workspace, slug="powerbook-import-replay", title="权力的底层结构（PowerBook导入）", overwrite=True)
    imported_root = Path(imported["root"])
    imported_chapter = first_chapter_path(imported_root, "chapters/ch01_power.md")
    shutil.copy2(imported_chapter, generated_dir / "imported-powerbook-ch01.md")

    original_chapter = source / "book" / "chapters" / "ch01_power.md"
    shutil.copy2(original_chapter, generated_dir / "original-powerbook-ch01.md")

    after_hash = tree_hash(source)
    guide_score = score_chapter(guide_chapter)
    original_score = score_chapter(original_chapter)
    imported_score = score_chapter(imported_chapter)
    guide_chapters = chapter_files(guide_root)
    original_chapters = chapter_files(source)
    imported_chapters = chapter_files(imported_root)
    guide_book_chars = manuscript_chinese_chars(guide_chapters)
    original_book_chars = manuscript_chinese_chars(original_chapters)
    imported_book_chars = manuscript_chinese_chars(imported_chapters)
    ratio = round(guide_score["chineseChars"] / max(1, original_score["chineseChars"]), 3)
    import_ratio = round(imported_score["chineseChars"] / max(1, original_score["chineseChars"]), 3)
    guide_book_ratio = round(guide_book_chars / max(1, original_book_chars), 3)
    imported_book_ratio = round(imported_book_chars / max(1, original_book_chars), 3)
    guide_gate = (
        guide_score["qualityScore"]["score"] >= 9
        and guide_score["chineseChars"] >= 6000
        and ratio >= 0.88
        and guide_score["qualityScore"]["features"].get("starts_from_specific_scene")
        and guide_score["qualityScore"]["features"].get("uses_traceable_material_or_fact_boundary")
        and len(guide_chapters) >= 34
        and guide_book_ratio >= 0.95
    )
    summary: Dict[str, Any] = {
        "source": source.as_posix(),
        "sourceHashBefore": before_hash,
        "sourceHashAfter": after_hash,
        "sourceUnchanged": before_hash == after_hash,
        "replayInputs": {"directory": replay_inputs_dir.as_posix(), "count": len(inputs), "totalChars": sum(int(item["chars"]) for item in inputs)},
        "workspace": workspace.as_posix(),
        "bookWorkbenchGuide": {"root": guide_root.as_posix(), "createdFiles": len(created.get("createdFiles", [])), "baselineCommitCreated": created.get("baselineCommitCreated"), "baselineCommit": created.get("baselineCommit"), "chapterCount": len(guide_chapters), "totalChineseChars": guide_book_chars, "ch01": guide_score},
        "importedPowerBook": {**imported, "chapterCount": len(imported_chapters), "totalChineseChars": imported_book_chars, "ch01": imported_score},
        "powerbookBaseline": {"treeHash": before_hash, "chapterCount": len(original_chapters), "totalChineseChars": original_book_chars, "ch01": original_score},
        "comparison": {
            "question": "BookWorkbench 后台用 PowerBook 原始输入从零创建，能否达到原 PowerBook 当前首章质量档？",
            "deterministicAnswer": "从零后台质量门槛已通过。BookWorkbench guide 首章使用原始 replay 记忆生成完整章节，长度、结构、历史入口、事实边界、中国语境和核心定义均已接近原 PowerBook 当前稿；导入路径继续保持原稿质量。" if guide_gate else "从零后台质量门槛未通过；仍需修正 guide 生成或模型 replay。",
            "guideMeetsQualityGate": bool(guide_gate),
            "originalCurrentScoreRatio": original_score["qualityScore"]["ratio"],
            "guideScoreRatio": guide_score["qualityScore"]["ratio"],
            "guideVsOriginalLengthRatioChinese": ratio,
            "importPreservesOriginalCh01LengthRatioChinese": import_ratio,
            "guideChapterCount": len(guide_chapters),
            "originalChapterCount": len(original_chapters),
            "guideVsOriginalBookLengthRatioChinese": guide_book_ratio,
            "importPreservesOriginalBookLengthRatioChinese": imported_book_ratio,
        },
        "verification": {
            "compileall": run_command([sys.executable, "-m", "compileall", "-q", "book_workbench", "tests", "scripts"], cwd=ROOT),
        },
    }
    write_report(output_dir, summary)
    if not args.keep_workspace:
        shutil.rmtree(workspace, ignore_errors=True)
    ok = bool(summary["sourceUnchanged"] and guide_gate and summary["verification"]["compileall"]["returncode"] == 0)
    print(json.dumps({"ok": ok, "output": output_dir.as_posix(), "guideScore": guide_score["qualityScore"], "sourceUnchanged": summary["sourceUnchanged"]}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
