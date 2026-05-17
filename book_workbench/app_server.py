"""Stdlib local web app for BookWorkbench.

The app is deliberately thin: browser actions call the same
``RuntimeOrchestrator`` methods as the CLI, so all manuscript writes still pass
through patch validation, preview, apply, and audit logging.
"""

from __future__ import annotations

import json
import secrets
import threading
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Mapping
from urllib.parse import parse_qs, unquote, urlparse

from .annotation_engine import annotation_to_dict, classification_summary, list_annotations
from .audit import AuditLog
from .codex_client import CodexAppServerClient
from .codex_workflow import build_revise_with_annotations_prompt, patch_has_changes, proposal_matches_annotation_scope, summarize_codex_result, validation_is_valid
from .discussion_engine import append_discussion, list_discussions
from .patch_engine import validate_patch
from .project import (
    ProjectLoadError,
    chapter_summaries,
    index_markdown_blocks,
    load_project,
    manuscript_word_count,
    markdown_title,
    safe_chapter_path,
    write_block_index,
)
from .project_creator import ProjectCreationError, create_book_project
from .workspace import list_projects, project_summary, resolve_workspace_project
from .runtime import RuntimeErrorBase, RuntimeOrchestrator


APP_TITLE = "书稿工作台本地版"
DEFAULT_AI_REVISE_TIMEOUT_SECONDS = 30.0


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>书稿工作台</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7fb;
      --panel: #ffffff;
      --panel-soft: #f8faff;
      --line: #e4e9f3;
      --muted: #75819a;
      --text: #111827;
      --text-soft: #40506a;
      --purple: #5b3df5;
      --purple-2: #7c5cff;
      --purple-soft: #eeeafe;
      --green: #31a66a;
      --green-soft: #eaf8ee;
      --orange: #f58228;
      --orange-soft: #fff0e4;
      --red: #e5484d;
      --red-soft: #fff0f1;
      --nav: #071224;
      --shadow: 0 18px 48px rgba(15, 23, 42, .08);
      --radius: 18px;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--text); }
    button, input, textarea, select { font: inherit; }
    button { cursor: pointer; }
    button:disabled { cursor: not-allowed; opacity: .48; filter: grayscale(.12); }
    .hidden { display: none !important; }
    .app { min-height: 100vh; display: grid; grid-template-columns: 304px 1fr; }
    .sidebar {
      position: sticky; top: 0; height: 100vh; padding: 28px 24px;
      display: flex; flex-direction: column; gap: 22px;
      background: radial-gradient(circle at 18% 0%, rgba(100, 80, 255, .26), transparent 28%), linear-gradient(180deg, #071224 0%, #050d19 100%);
      color: #eaf0ff;
    }
    .brand { display: flex; gap: 14px; align-items: center; margin-bottom: 10px; }
    .logo { width: 52px; height: 52px; border-radius: 12px; display: grid; place-items: center; background: linear-gradient(135deg, #6c55ff, #1c1d68); box-shadow: 0 8px 22px rgba(91, 61, 245, .35); font-size: 29px; }
    .brand strong { display: block; font-size: 19px; letter-spacing: -.02em; }
    .brand span { display: block; color: #93a4c0; margin-top: 4px; font-size: 14px; }
    .nav { display: grid; gap: 8px; }
    .nav button { border: 0; width: 100%; color: #dbe6fb; background: transparent; text-align: left; display: flex; align-items: center; gap: 14px; padding: 14px 16px; border-radius: 12px; font-size: 17px; }
    .nav button:hover { background: rgba(255,255,255,.08); }
    .nav button.active { background: linear-gradient(90deg, rgba(100, 80, 255, .72), rgba(85, 64, 190, .58)); color: #fff; }
    .nav button:disabled { opacity: .38; cursor: not-allowed; }
    .nav .icon { width: 22px; text-align: center; font-size: 20px; }
    .sidebar-spacer { flex: 1; }
    .book-card, .help-card { border: 1px solid rgba(255,255,255,.06); border-radius: 14px; background: rgba(255,255,255,.07); padding: 14px; box-shadow: 0 12px 30px rgba(0,0,0,.18); }
    .book-card { display: flex; gap: 12px; align-items: center; }
    .cover { width: 58px; height: 58px; border-radius: 10px; display: grid; place-items: center; background: linear-gradient(135deg, #eef2ff, #d8dcff); color: #4f46e5; font-size: 24px; }
    .book-card strong { display: block; font-size: 16px; }
    .book-card span, .storage span, .help-card { color: #aebbd3; font-size: 13px; }
    .storage { display: grid; gap: 10px; padding: 0 14px; }
    .storage-row { display: flex; justify-content: space-between; }
    .bar { height: 8px; border-radius: 999px; background: rgba(255,255,255,.16); overflow: hidden; }
    .bar > i { display: block; height: 100%; width: 0%; border-radius: inherit; background: linear-gradient(90deg, #6e55ff, #a08dff); }
    .help-card { display: flex; justify-content: space-between; align-items: center; padding: 16px; }
    .main { min-width: 0; padding: 30px 34px 26px; }
    .topbar { display: flex; align-items: center; gap: 20px; margin-bottom: 26px; }
    .title-wrap { flex: 1; min-width: 0; }
    h1 { margin: 0; font-size: 30px; letter-spacing: -.035em; line-height: 1.15; }
    .subtitle { margin-top: 9px; color: var(--muted); font-size: 14px; }
    .search { width: 390px; height: 48px; border: 1px solid #d9e1ef; background: #fff; border-radius: 14px; display: flex; align-items: center; gap: 10px; padding: 0 14px; color: var(--muted); box-shadow: 0 8px 24px rgba(15,23,42,.04); }
    .search input { border: 0; outline: 0; flex: 1; min-width: 0; color: var(--text); }
    .kbd { border: 1px solid var(--line); border-radius: 8px; padding: 2px 7px; background: #f3f6fb; color: #8b95a8; }
    .avatar { display: flex; align-items: center; gap: 10px; color: #101828; font-weight: 650; }
    .avatar-img { width: 42px; height: 42px; border-radius: 50%; background: radial-gradient(circle at 45% 30%, #dbe4ff, #7c5cff 42%, #18213b 72%); }
    .grid { display: grid; gap: 18px; }
    .dashboard { grid-template-columns: minmax(0, 1fr) 390px; align-items: start; }
    .editor, .diff, .rules-layout { grid-template-columns: minmax(0, 1fr) 390px; align-items: start; }
    .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; }
    .card { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow); }
    .panel { padding: 18px; }
    .panel-title { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 16px; }
    .panel-title h2, .panel-title h3 { margin: 0; font-size: 18px; }
    .primary-btn, .ghost-btn, .danger-btn { min-height: 44px; border-radius: 10px; border: 1px solid #dfe5f0; padding: 0 14px; background: #fff; color: var(--text-soft); font-weight: 650; }
    .primary-btn { border: 0; color: #fff; background: linear-gradient(135deg, var(--purple-2), var(--purple)); box-shadow: 0 10px 22px rgba(91,61,245,.22); }
    .danger-btn { color: var(--red); border-color: #ffb9bf; background: #fff; }
    .link-btn { border: 0; background: transparent; color: var(--purple); font-weight: 700; }
    .muted { color: var(--muted); }
    .ok { color: var(--green); }
    .bad { color: var(--red); }
    .empty-state { min-height: 520px; display: grid; place-items: center; text-align: center; padding: 46px; }
    .empty-state .empty-icon { width: 92px; height: 92px; border-radius: 28px; display: grid; place-items: center; margin: 0 auto 18px; background: var(--purple-soft); color: var(--purple); font-size: 42px; }
    .empty-state h2 { margin: 0 0 10px; font-size: 28px; }
    .empty-state p { max-width: 620px; margin: 0 auto 22px; line-height: 1.8; color: var(--muted); }
    .project-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; }
    .project-card { text-align: left; border: 1px solid var(--line); border-radius: 16px; background: #fff; padding: 18px; box-shadow: var(--shadow); min-height: 150px; }
    .project-card:hover { border-color: #c9c3ff; transform: translateY(-1px); }
    .project-card strong { display: block; font-size: 20px; margin: 10px 0 8px; }
    .project-card .meta { color: var(--muted); line-height: 1.7; }
    .metric { padding: 16px; min-height: 118px; min-width: 0; overflow: hidden; display: grid; grid-template-columns: 44px minmax(130px, 1fr); align-items: center; gap: 12px; writing-mode: horizontal-tb !important; }
    .metric-icon, .small-icon { width: 44px; height: 44px; border-radius: 16px; display: grid; place-items: center; font-size: 22px; writing-mode: horizontal-tb !important; }
    .metric-icon.purple, .small-icon.purple { background: var(--purple-soft); color: var(--purple); }
    .metric-icon.green, .small-icon.green { background: var(--green-soft); color: var(--green); }
    .metric-icon.blue, .small-icon.blue { background: #eaf2ff; color: #3f7fe8; }
    .metric-icon.orange, .small-icon.orange { background: var(--orange-soft); color: var(--orange); }
    .metric .label { color: var(--muted); font-size: 14px; line-height: 1.25; white-space: nowrap; word-break: keep-all; overflow-wrap: normal; writing-mode: horizontal-tb !important; }
    .metric .num { display: block; margin: 4px 0; font-size: clamp(24px, 3vw, 34px); line-height: 1; font-weight: 800; letter-spacing: -.03em; white-space: nowrap; writing-mode: horizontal-tb !important; }
    .metric .note { display: block; font-size: 13px; line-height: 1.35; white-space: nowrap; word-break: keep-all; overflow-wrap: normal; overflow: hidden; text-overflow: ellipsis; writing-mode: horizontal-tb !important; }
    .metric-body { min-width: 0; width: 100%; overflow: hidden; display: grid; gap: 2px; writing-mode: horizontal-tb !important; }
    .table-scroll { width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; }
    .chapter-table { width: 100%; min-width: 760px; border-collapse: collapse; font-size: 14px; table-layout: auto; writing-mode: horizontal-tb !important; }
    .chapter-table th { color: #7b86a0; text-align: left; font-weight: 600; padding: 12px 10px; border-bottom: 1px solid var(--line); white-space: nowrap; word-break: keep-all; overflow-wrap: normal; writing-mode: horizontal-tb; }
    .chapter-table td { padding: 12px 10px; border-bottom: 1px solid #edf1f6; white-space: nowrap; word-break: keep-all; overflow-wrap: normal; writing-mode: horizontal-tb; }
    .chapter-table th:first-child, .chapter-table td:first-child { min-width: 260px; white-space: normal; }
    .chapter-table th:nth-child(4), .chapter-table td:nth-child(4) { min-width: 112px; text-align: right; white-space: nowrap; }
    .chapter-words { font-variant-numeric: tabular-nums; writing-mode: horizontal-tb !important; white-space: nowrap; }
    .chapter-table tr:hover { background: #fafbff; }
    .status-pill { display: inline-flex; align-items: center; gap: 5px; border-radius: 8px; padding: 4px 8px; font-size: 12px; font-weight: 650; }
    .status-draft { background: var(--orange-soft); color: var(--orange); }
    .status-reviewed { background: var(--green-soft); color: var(--green); }
    .status-locked { background: #eaf2ff; color: #3f7fe8; }
    .status-unreviewed { background: var(--purple-soft); color: var(--purple); }
    .side-stack { display: grid; gap: 14px; }
    .side-stack > .panel { padding: 16px; }
    .toolbar { min-height: 76px; padding: 14px 16px; display: flex; align-items: center; gap: 14px; border-bottom: 1px solid var(--line); }
    .toolbar .muted { white-space: nowrap; }
    .stats .muted { white-space: nowrap; }
    .select-like { border: 1px solid #dfe5f0; border-radius: 10px; padding: 10px 14px; background: #fff; min-width: 190px; font-weight: 700; }
    .toolbar .spacer, .spacer { flex: 1; }
    .editor-card { overflow: hidden; }
    .doc { padding: 38px 42px 24px; background: #fff; min-height: 560px; }
    .doc h2 { font-size: 25px; margin: 0 0 28px; }
    .paragraph { display: grid; grid-template-columns: 92px minmax(0, 1fr) 110px; gap: 16px; align-items: start; margin: 22px 0; line-height: 2; font-size: 17px; }
    .pid { color: #7d8aa4; font-size: 13px; padding-top: 8px; }
    .ptext { padding: 3px 6px; border-radius: 8px; white-space: pre-wrap; user-select: text; }
    .doc ::selection { background: rgba(101, 80, 255, .24); }
    .paragraph.annotated .ptext { background: linear-gradient(90deg, rgba(101,80,255,.16), rgba(101,80,255,.05)); }
    .selection-menu { position: fixed; z-index: 70; width: min(310px, calc(100vw - 24px)); padding: 12px; border: 1px solid #d8ddff; border-radius: 16px; background: #fff; box-shadow: 0 18px 52px rgba(15,23,42,.18); display: grid; gap: 10px; }
    .selection-menu-title { color: var(--muted); font-size: 12px; font-weight: 800; letter-spacing: .04em; text-transform: uppercase; }
    .selection-preview { max-height: 74px; overflow: auto; border-radius: 10px; padding: 9px 10px; background: #f7f6ff; color: var(--text-soft); font-size: 13px; line-height: 1.55; }
    .tag { display: inline-flex; align-items: center; justify-content: center; min-width: 58px; padding: 3px 8px; border-radius: 7px; background: var(--purple-soft); color: var(--purple); font-size: 13px; font-weight: 750; }
    .status-badge { display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-width: 64px; border-radius: 999px; padding: 4px 10px; background: var(--green-soft); color: var(--green); font-size: 12px; font-weight: 800; white-space: nowrap; }
    .status-badge::before { content: ""; width: 7px; height: 7px; border-radius: 999px; background: currentColor; }
    .status-badge.neutral { background: #eef2f7; color: #64728a; }
    .editor-footer { display: flex; gap: 24px; align-items: center; border-top: 1px solid var(--line); padding: 14px 24px; color: #6b7892; font-size: 14px; }
    .annotation-card { border: 1px solid var(--line); border-radius: 14px; padding: 14px; margin-bottom: 10px; background: #fff; line-height: 1.65; text-align: left; width: 100%; }
    .annotation-list-note { margin: 0 0 12px; padding: 10px 12px; border-radius: 12px; background: var(--panel-soft); color: var(--muted); font-size: 13px; }
    .discussion-card { border: 1px solid #dfe5f0; border-radius: 14px; padding: 14px; margin-bottom: 12px; background: #fbfcff; }
    .discussion-card strong { color: var(--purple); }
    .discussion-card p { margin: 10px 0 0; line-height: 1.7; }
    .annotation-card.active { border-color: #d1c9ff; background: #fbfaff; box-shadow: 0 10px 24px rgba(91,61,245,.08); }
    .annotation-card:disabled { opacity: .72; cursor: not-allowed; background: #fafbff; }
    .annotation-head { display: flex; justify-content: space-between; gap: 8px; color: var(--muted); font-size: 13px; }
    .annotation-card strong { color: var(--purple); }
    .annotation-card p { margin: 12px 0; line-height: 1.75; }
    .ai-item { display: grid; grid-template-columns: 40px minmax(0, 1fr) auto; gap: 12px; align-items: center; padding: 12px 0; border-bottom: 1px solid #edf1f6; line-height: 1.55; }
    .button-row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 14px; }
    .button-row.single { grid-template-columns: 1fr; }
    .diff-top { display: flex; align-items: center; gap: 16px; margin-bottom: 18px; }
    .back-btn { width: 46px; height: 46px; border-radius: 10px; border: 1px solid #dfe5f0; background: #fff; color: var(--text-soft); font-size: 24px; }
    .change-meta { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0; overflow: hidden; margin-bottom: 16px; }
    .meta-cell { padding: 14px 18px; border-right: 1px solid var(--line); display: flex; gap: 12px; align-items: center; }
    .meta-cell:last-child { border-right: 0; }
    .diff-columns { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    .diff-col { padding: 22px; background: #fff; border: 1px solid var(--line); border-radius: 16px; min-height: 340px; }
    .diff-line { display: grid; grid-template-columns: 34px 1fr 18px; gap: 10px; padding: 13px 15px; border-radius: 12px; margin: 9px 0; line-height: 1.8; }
    .diff-line.minus { background: #ffe9ec; border-left: 4px solid #e11d48; }
    .diff-line.plus { background: #e6faee; border-left: 4px solid #059669; }
    .diff-line .ln { color: #8d99ad; }
    .diff-reason { margin-top: 16px; padding: 18px 20px; }
    .diff-reason.collapsed .diff-body { display: none; }
    .diff-reason .link-btn { border-radius: 8px; padding: 6px 10px; }
    .diff-raw { margin-top: 12px; background: #0f172a; color: #e2e8f0; border-radius: 12px; padding: 16px; max-height: 320px; overflow: auto; font-size: 14px; line-height: 1.55; }
    .check-list { border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
    .check-row { display: grid; grid-template-columns: 28px 1fr auto; gap: 10px; padding: 12px; border-bottom: 1px solid var(--line); align-items: center; }
    .check-row:last-child { border-bottom: 0; }
    .commit-box { border: 1px solid var(--line); border-radius: 10px; padding: 12px; background: #fbfcff; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; overflow: auto; }
    .rules-main { display: grid; grid-template-columns: 42% minmax(0, 1fr); gap: 18px; align-items: start; }
    .tabs { display: flex; gap: 22px; border-bottom: 1px solid var(--line); padding: 0 16px; }
    .tab { border: 0; background: transparent; color: #64728a; padding: 14px 0; font-weight: 650; }
    .tab.active { color: var(--purple); box-shadow: inset 0 -2px 0 var(--purple); }
    .filter-panel { margin: 0 12px 10px; padding: 12px; border-radius: 14px; background: var(--panel-soft); border: 1px solid var(--line); display: grid; gap: 10px; }
    .filter-chips { display: flex; flex-wrap: wrap; gap: 8px; }
    .filter-chip { border: 1px solid #dfe5f0; background: #fff; color: var(--text-soft); border-radius: 999px; padding: 7px 11px; font-weight: 700; }
    .filter-chip.active { border-color: #c9c3ff; background: var(--purple-soft); color: var(--purple); }
    .rule-list { padding: 10px 12px 14px; }
    .rule-row { border: 1px solid transparent; border-bottom-color: #edf1f6; border-radius: 12px; padding: 14px; display: grid; gap: 8px; }
    .rule-row.active { border-color: #c9c3ff; background: #fbfaff; box-shadow: inset 3px 0 0 var(--purple); }
    .rule-meta { display: flex; justify-content: space-between; color: var(--muted); font-size: 13px; }
    .path-inline { display: inline-block; max-width: min(720px, 70vw); overflow: hidden; text-overflow: ellipsis; vertical-align: bottom; white-space: nowrap; }
    .modal-backdrop { position: fixed; inset: 0; background: rgba(15,23,42,.46); display: grid; place-items: center; padding: 28px; z-index: 50; }
    .modal { width: min(760px, 100%); max-height: calc(100vh - 56px); overflow: auto; border-radius: 22px; background: #fff; box-shadow: 0 26px 80px rgba(15,23,42,.28); border: 1px solid #e5e7eb; }
    .modal header { display: flex; justify-content: space-between; align-items: center; padding: 20px 22px; border-bottom: 1px solid var(--line); }
    .modal header h2 { margin: 0; }
    .modal form, .modal-body { padding: 22px; display: grid; gap: 16px; }
    .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
    label.field { display: grid; gap: 8px; color: #44516a; font-weight: 650; }
    .field input, .field textarea { width: 100%; border: 1px solid #dfe5f0; border-radius: 12px; padding: 12px; outline: none; background: #fff; color: var(--text); }
    .field textarea { min-height: 92px; resize: vertical; line-height: 1.6; }
    .modal-actions { display: flex; justify-content: flex-end; gap: 12px; padding: 0 22px 22px; }
    .toast { position: fixed; left: 50%; bottom: 26px; transform: translate(-50%, 20px); opacity: 0; pointer-events: none; background: #101828; color: #fff; padding: 12px 18px; border-radius: 999px; box-shadow: 0 16px 40px rgba(15,23,42,.2); transition: .18s ease; z-index: 80; }
    .toast.show { opacity: 1; transform: translate(-50%, 0); }
    @media (max-width: 1160px) {
      .app { grid-template-columns: 88px 1fr; }
      .sidebar { padding: 20px 14px; }
      .brand div:not(.logo), .nav span:not(.icon), .book-card, .storage, .help-card { display: none; }
      .nav button { justify-content: center; padding: 14px; }
      .main { padding: 24px; }
      .dashboard, .editor, .diff, .rules-layout, .rules-main { grid-template-columns: 1fr; }
      .stats { grid-template-columns: repeat(2, minmax(220px, 1fr)); }
      .search { width: 300px; }
    }
    @media (max-width: 760px) {
      .app { display: block; }
      .sidebar { position: static; height: auto; }
      .nav { grid-template-columns: repeat(3, 1fr); }
      .topbar { flex-wrap: wrap; }
      .search { order: 3; width: 100%; }
      .main { padding: 18px; }
      .topbar { align-items: flex-start; }
      .avatar { display: none; }
      .stats, .diff-columns, .change-meta, .form-grid { grid-template-columns: 1fr; }
      .toolbar, .diff-top, .editor-footer { flex-wrap: wrap; }
      .side-stack { gap: 12px; }
      .editor > aside.side-stack { display: block; }
      .editor > aside.side-stack > .panel { margin-bottom: 12px; }
      .paragraph { grid-template-columns: 1fr; gap: 4px; }
      .doc { padding: 24px 18px; }
      .button-row, .button-row.single { grid-template-columns: 1fr; }
      .primary-btn, .ghost-btn, .danger-btn, .back-btn { min-height: 44px; }
    }
  </style>
</head>
<body data-app-title="书稿工作台本地版">
  <div class="app">
    <aside class="sidebar">
      <div class="brand"><div class="logo">🪶</div><div><strong>书稿工作台</strong><span>本地写作与批注</span></div></div>
      <nav class="nav" aria-label="主导航">
        <button class="active" data-view="dashboard"><span class="icon">⌂</span><span>项目</span></button>
        <button data-view="editor" data-project-only><span class="icon">▤</span><span>文稿</span></button>
        <button data-view="annotations" data-project-only><span class="icon">☵</span><span>批注</span></button>
        <button data-view="discussions" data-project-only><span class="icon">✎</span><span>讨论</span></button>
        <button data-view="rules" data-project-only><span class="icon">▱</span><span>规则</span></button>
        <button data-view="diff" data-project-only><span class="icon">↺</span><span>版本</span></button>
        <button data-view="export" data-project-only><span class="icon">⇱</span><span>导出</span></button>
        <button data-view="settings"><span class="icon">⚙</span><span>设置</span></button>
      </nav>
      <div class="sidebar-spacer"></div>
      <div class="book-card"><div class="cover">□</div><div><strong id="sideBookTitle">未打开项目</strong><span id="sideBookMeta">请选择或新建书稿项目</span></div><span>⌄</span></div>
      <div class="storage"><div class="storage-row"><span>当前工作区</span><span id="workspaceShort">本地</span></div><div class="bar"><i id="workspaceBar"></i></div></div>
      <div class="help-card"><span>ⓘ 安全模式：先审后改</span><span>✓</span></div>
    </aside>

    <main class="main">
      <header class="topbar">
        <div class="title-wrap"><h1 id="pageTitle">项目</h1><div id="pageSubtitle" class="subtitle">本地工作台已启动。先选择或新建一个书稿项目。</div></div>
        <label class="search">⌕ <input placeholder="搜索章节、文稿、批注..." aria-label="搜索"><span class="kbd">快捷键</span></label>
        <div class="avatar"><div class="avatar-img"></div><span>本地作者</span></div>
      </header>

      <section id="view-dashboard" class="view grid dashboard">
        <div class="grid" id="dashboardMain"></div>
        <aside class="side-stack" id="dashboardSide"></aside>
      </section>

      <section id="view-editor" class="view grid editor hidden">
        <div class="card editor-card">
          <div class="toolbar"><div class="select-like" id="chapterSelect">未选择章节</div><span class="status-pill status-draft" id="editorStatus">草稿</span><span class="muted" id="editorWords">字数：0</span><div class="spacer"></div><button class="ghost-btn" id="saveBtn">✓ 保存</button><button class="ghost-btn" id="manualPatchBtn">生成手动修改建议</button><button class="primary-btn" id="generateSuggestionBtn">✦ 智能处理批注</button></div>
          <article class="doc" id="docView"></article>
          <div class="selection-menu hidden" id="selectionMenu" role="menu" aria-label="选中文本操作">
            <div class="selection-menu-title">选中文本</div>
            <div class="selection-preview" id="selectionPreview"></div>
            <button class="primary-btn" type="button" id="selectionAddAnnotationBtn">添加批注</button>
          </div>
          <footer class="editor-footer"><span id="chapterWordCount">本章字数：0</span><span>所有正文修改必须先预览差异</span><span>运行时安全边界 <span class="ok">●</span></span><span class="spacer"></span><span id="selectionInfo">可选中文本后添加批注</span></footer>
        </div>
        <aside class="side-stack">
          <section class="card panel"><div class="panel-title"><h3>批注与智能建议</h3><button class="ghost-btn" id="addAnnotationGlobalBtn">添加批注</button></div><div class="tabs"><button class="tab active" data-annotation-tab="annotations">批注 (<span id="annotationCount">0</span>)</button><button class="tab" data-annotation-tab="suggestions">智能建议</button></div><div id="annotationPanel" class="mt"></div></section>
          <section class="card panel"><div class="panel-title"><h3>智能解读与建议 ⓘ</h3><span class="muted">先生成修改建议</span></div><div id="aiActionList"></div><div class="button-row"><button class="primary-btn" id="reviseCurrentBtn">处理当前批注</button><button class="ghost-btn" id="addRuleBtn">加入规则库</button></div><div class="button-row single"><button class="ghost-btn" id="laterBtn">稍后处理</button></div></section>
          <section class="card panel"><div class="stats" style="grid-template-columns:repeat(3,1fr); gap:8px"><div><strong id="ruleMatchRate">—</strong><br><span class="muted">规则匹配</span></div><div><strong class="bad" id="pendingAnnotationCount">0</strong><br><span class="muted">待处理批注</span></div><div><strong class="ok">✓</strong><br><span class="muted">先审后改</span></div></div></section>
        </aside>
      </section>

      <section id="view-diff" class="view grid diff hidden">
        <div>
          <div class="diff-top"><button class="back-btn" id="backToEditorBtn">‹</button><h1 style="font-size:24px" id="diffTitle">修改差异审核</h1><span class="tag" id="diffBlockTag">—</span><div class="spacer"></div><button class="ghost-btn" id="refreshDiffBtn">重新预览</button></div>
          <div class="card change-meta"><div class="meta-cell"><span class="small-icon purple">☵</span><div><span class="muted">来源批注</span><br><strong id="sourceAnnotation">—</strong></div></div><div class="meta-cell"><span class="small-icon purple">✦</span><div><span class="muted">应用规则</span><br><strong id="rulesUsed">—</strong></div></div><div class="meta-cell"><span class="small-icon blue">▣</span><div><span class="muted">写入方式</span><br><strong>安全应用</strong></div></div><div class="meta-cell"><span class="small-icon green">✓</span><div><span class="muted">状态</span><br><strong id="patchValidity">待预览</strong></div></div></div>
          <div class="diff-columns"><div class="diff-col"><h3>原文（修改前） <span class="tag" id="beforeBlockTag">—</span></h3><div id="beforeLines"></div></div><div class="diff-col"><h3>建议（修改后） <span class="tag" id="afterBlockTag">—</span></h3><div id="afterLines"></div></div></div>
          <section class="card diff-reason" id="diffReasonCard"><div class="panel-title"><h3>ⓘ 修改原因 / 运行时校验</h3><button class="link-btn" id="toggleDiffReasonBtn" aria-expanded="true">收起</button></div><div class="diff-body"><p id="changeReason" class="muted">所有智能输出只能在这里预览，接受后才写入。</p><pre class="diff-raw" id="rawDiff"></pre></div></section>
        </div>
        <aside class="side-stack"><div class="button-row" style="grid-template-columns:1fr 1fr 1fr"><button class="danger-btn" id="rejectPatchBtn">拒绝</button><button class="ghost-btn" id="partialPatchBtn" disabled title="暂不可用：当前只支持接受完整安全修改">部分应用（暂不可用）</button><button class="primary-btn" id="acceptPatchBtn">接受并提交</button></div><div class="button-row single hidden" id="invalidPatchActions"><button class="ghost-btn" id="regeneratePatchBtn">重新生成建议</button><button class="ghost-btn" id="remapAnnotationBtn">重新定位批注</button></div><section class="card panel"><div class="panel-title"><h3>本次变更</h3></div><div class="check-list" id="changeCheckList"></div><h3 class="mt">版本记录预览</h3><div class="commit-box" id="commitPreview">等待修改建议</div></section><section class="card panel"><div class="panel-title"><h3>影响范围</h3></div><div class="check-list"><div class="check-row"><span>▤</span><span>当前段落</span><strong id="impactBlock">—</strong></div><div class="check-row"><span>🔒</span><span>锁定章节</span><strong class="ok">自动拒绝</strong></div><div class="check-row"><span>⊕</span><span>其他章节</span><strong>不修改</strong></div></div></section></aside>
      </section>

      <section id="view-rules" class="view rules-layout hidden"><div class="rules-main"><section class="card"><div class="toolbar"><button class="ghost-btn" id="newRuleBtn" disabled title="暂不可用：规则目前由批注提炼生成">＋ 新建规则（暂不可用）</button><button class="ghost-btn" id="ruleFilterBtn" aria-expanded="false" aria-controls="ruleFilterPanel">≋ 筛选</button><button class="ghost-btn" id="batchApplyBtn" disabled title="暂不可用：批量应用需要先生成修改建议">▣ 批量应用（暂不可用）</button></div><div class="tabs" id="ruleTabs"><button class="tab active" data-rule-filter="all">全部（<span id="ruleCount">0</span>）</button><button class="tab" data-rule-filter="style">风格</button><button class="tab" data-rule-filter="structure">结构</button><button class="tab" data-rule-filter="setting">设定</button></div><div class="filter-panel hidden" id="ruleFilterPanel"><strong>规则筛选</strong><div class="filter-chips"><button class="filter-chip active" data-rule-filter="all">全部</button><button class="filter-chip" data-rule-filter="style">风格</button><button class="filter-chip" data-rule-filter="structure">结构</button><button class="filter-chip" data-rule-filter="setting">设定</button></div><span class="muted" id="ruleFilterSummary">显示全部规则</span></div><div class="rule-list" id="ruleList"></div></section><div class="grid"><section class="card panel" id="ruleDetail"></section><section class="card panel"><h3>规则传播安全边界</h3><div class="check-list"><div class="check-row"><span>✓</span><span>只影响草稿 / 未审阅</span><span class="status-badge">开启</span></div><div class="check-row"><span>🔒</span><span>已审阅章节需二次确认</span><span class="status-badge">开启</span></div><div class="check-row"><span>🔒</span><span>已锁定章节永不修改</span><span class="status-badge">开启</span></div></div><div class="button-row single"><button class="ghost-btn" id="previewRuleImpactBtn" disabled title="暂不可用：规则影响预览尚未接入运行时">预览影响（暂不可用）</button></div></section></div></div></section>
      <section id="view-annotations" class="view hidden"><section class="card panel"><div class="panel-title"><h2>批注中心</h2><button class="primary-btn" id="annotationToEditorBtn">打开文稿处理</button></div><div id="annotationCenter"></div></section></section>
      <section id="view-discussions" class="view grid dashboard hidden"><section class="card panel"><div class="panel-title"><h2>项目讨论</h2><span class="muted">写作想法会进入独立记录，不改正文</span></div><form id="discussionForm" class="grid"><label class="field">讨论内容<textarea id="discussionTextInput" required placeholder="例如：这一章想讨论人物动机、节奏或设定。"></textarea></label><div class="button-row single"><button class="primary-btn" id="submitDiscussionBtn" type="submit">保存讨论</button></div></form></section><aside class="side-stack"><section class="card panel"><div class="panel-title"><h3>讨论记录</h3><span class="tag" id="discussionCount">0</span></div><div id="discussionList"></div></section><section class="card panel"><h3>安全说明</h3><p class="muted">讨论只作为上下文记录，不会直接修改章节正文；改稿仍必须先生成修改建议并预览差异。</p></section></aside></section>
      <section id="view-export" class="view hidden"><section class="card panel"><h2>导出项目</h2><p class="muted">当前版本保持本地书稿项目结构，可直接复制项目目录。导出适配器后续会接入文字文档与便携文档往返流程。</p><button class="ghost-btn" id="copyProjectPathBtn">复制项目路径</button><pre class="diff-raw" id="projectPathBox"></pre></section></section>
      <section id="view-settings" class="view hidden"><section class="card panel"><h2>设置 / 安全中心</h2><div class="check-list"><div class="check-row"><span>🛡</span><span>智能处理只生成建议，不自动写入</span><span class="status-badge">开启</span></div><div class="check-row"><span>🔒</span><span>锁定章节禁止修改</span><span class="status-badge">开启</span></div><div class="check-row"><span>▣</span><span>修改必须先生成修改建议</span><span class="status-badge">开启</span></div><div class="check-row"><span>⌘</span><span>智能服务</span><span class="status-badge neutral" id="settingsCodex">未检测</span></div><div class="check-row"><span>□</span><span>工作区</span><strong class="path-inline" id="settingsWorkspace" title="">—</strong></div></div></section></section>
    </main>
  </div>

  <div class="modal-backdrop hidden" id="projectModal" data-testid="new-project-modal">
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="projectModalTitle">
      <header><h2 id="projectModalTitle">新建书稿项目</h2><button class="ghost-btn" type="button" id="closeProjectModalBtn">关闭</button></header>
      <form id="projectForm" autocomplete="off">
        <div class="form-grid">
          <label class="field" for="projectTitleInput">书名<input id="projectTitleInput" name="title" required autocomplete="off" aria-label="书名" data-testid="project-title-input" placeholder="例如：我的第一本书"></label>
          <label class="field" for="projectSlugInput">本地目录名（可留空）<input id="projectSlugInput" name="slug" autocomplete="off" autocapitalize="none" spellcheck="false" aria-label="本地目录名" data-testid="project-slug-input" placeholder="可留空，系统会自动生成"></label>
        </div>
        <div class="form-grid">
          <label class="field" for="projectGenreInput">类型<input id="projectGenreInput" name="genre" autocomplete="off" aria-label="类型" data-testid="project-genre-input" placeholder="长篇小说 / 非虚构 / 剧本"></label>
          <label class="field" for="projectChapterTitleInput">第一章标题<input id="projectChapterTitleInput" name="chapterTitle" autocomplete="off" aria-label="第一章标题" data-testid="project-chapter-title-input" placeholder="第一章"></label>
        </div>
        <label class="field" for="projectPremiseInput">核心命题 / 简介<textarea id="projectPremiseInput" name="premise" autocomplete="off" aria-label="核心命题或简介" data-testid="project-premise-input" placeholder="这本书想写什么？可以先留空。"></textarea></label>
        <label class="field" for="projectStyleInput">风格偏好<textarea id="projectStyleInput" name="style" autocomplete="off" aria-label="风格偏好" data-testid="project-style-input" placeholder="例如：克制、具体、少解释心理。可以先留空。"></textarea></label>
        <label class="field" for="projectOpeningInput">开篇正文（可选；留空不会预置小说）<textarea id="projectOpeningInput" name="openingText" autocomplete="off" aria-label="开篇正文" data-testid="project-opening-input" placeholder="如果你还没开始写，就保持为空。"></textarea></label>
      </form>
      <div class="modal-actions"><button class="ghost-btn" type="button" id="cancelProjectBtn">取消</button><button class="primary-btn" type="submit" form="projectForm" id="submitProjectBtn">创建项目</button></div>
    </div>
  </div>

  <div class="modal-backdrop hidden" id="annotationModal" data-testid="annotation-modal">
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="annotationModalTitle">
      <header><h2 id="annotationModalTitle">添加批注</h2><button class="ghost-btn" type="button" id="closeAnnotationModalBtn">关闭</button></header>
      <form id="annotationForm" autocomplete="off">
        <input type="hidden" id="annotationFileInput">
        <input type="hidden" id="annotationBlockInput">
        <label class="field" for="annotationSelectedInput">选中文本<textarea id="annotationSelectedInput" required autocomplete="off" aria-label="选中文本" data-testid="annotation-selected-input"></textarea></label>
        <label class="field" for="annotationBodyInput">批注内容<textarea id="annotationBodyInput" required autocomplete="off" aria-label="批注内容" data-testid="annotation-body-input" placeholder="写下你的修改意见"></textarea></label>
      </form>
      <div class="modal-actions"><button class="ghost-btn" type="button" id="cancelAnnotationBtn">取消</button><button class="primary-btn" type="submit" form="annotationForm" id="submitAnnotationBtn">保存批注</button></div>
    </div>
  </div>

  <div class="toast" id="toast" role="status" aria-live="polite"></div>

  <script>
    const BOOKWORKBENCH_TOKEN = __BOOKWORKBENCH_TOKEN__;
    const state = { workspace: null, projects: [], project: null, annotations: [], discussions: [], audit: [], currentFile: null, currentChapter: null, lastPatch: null, lastPreview: null, activeView: "dashboard", selectedAnnotationId: null, activeAnnotationTab: "annotations", ruleFilter: "all", selectedRuleId: null, diffReasonCollapsed: false, selectionDraft: null };
    const $ = (id) => document.getElementById(id);
    function escapeHtml(value) { const span = document.createElement("span"); span.textContent = String(value ?? ""); return span.innerHTML; }
    function hasProject() { return !!(state.project && state.project.open !== false && state.project.root); }
    function toast(message) { const el = $("toast"); el.textContent = message; el.classList.add("show"); clearTimeout(toast.timer); toast.timer = setTimeout(() => el.classList.remove("show"), 2800); }
    async function api(path, options = {}) { const headers = { "Content-Type": "application/json", "X-BookWorkbench-Token": BOOKWORKBENCH_TOKEN, ...(options.headers || {}) }; const response = await fetch(path, { ...options, headers }); const text = await response.text(); let payload; try { payload = text ? JSON.parse(text) : {}; } catch (_) { payload = { raw: text }; } if (!response.ok) throw new Error(payload.error || response.statusText); return payload; }
    async function maybeApi(path) { try { return await api(path); } catch (_) { return null; } }
    function bookTitle() { const spec = state.project?.bookSpec || ""; const match = spec.match(/#\s*(《[^》]+》|.+?)\s*(?:Book SPEC|书稿设定)/); return match ? match[1].trim() : (state.project?.title || "未命名作品"); }
    function wordCount(text) { return String(text || "").replace(/<!--.*?-->/gs, "").replace(/\s+/g, "").length; }
    function statusLabel(status) { return ({ draft: "草稿", reviewed: "已审阅", locked: "已锁定", unreviewed: "未审阅", annotated: "已批注", briefed: "有审阅简报", revised: "已修订" })[status] || status || "草稿"; }
    function statusClass(status) { const normalized = ({ annotated: "unreviewed", briefed: "unreviewed", revised: "reviewed" })[status] || status || "draft"; return `status-${normalized}`; }
    function titleFromPath(path) { return String(path || "").split("/").pop().replace(/\.md$/, ""); }
    function splitLines(text) { const lines = String(text || "").split(/\\n+/).filter(Boolean); return lines.length ? lines : ["（空）"]; }
    function firstChapterFile() { const files = Object.keys(state.project?.blocks || {}).sort(); return files[0] || null; }
    function chapterSummary(file) { return state.project?.chapterSummaries?.[file] || {}; }
    function chapterTitle(file) { return chapterSummary(file).title || titleFromPath(file); }
    function formatNumber(value) { const number = Number(value || 0); return Number.isFinite(number) ? number.toLocaleString() : "0"; }
    function totalWordCount() { return Object.values(state.project?.chapterSummaries || {}).reduce((sum, item) => sum + Number(item.wordCount || 0), 0); }
    function blockAnnotation(blockId) { return state.annotations.find((item) => item.block_id === blockId || item.blockId === blockId); }
    function selectedAnnotation() { const openItems = state.annotations.filter((item) => item.status === "open"); if (state.selectedAnnotationId) { const selected = openItems.find((item) => item.id === state.selectedAnnotationId); if (selected) return selected; } return openItems[0] || null; }
    function ruleTypeLabel(type) { return ({ all: "全部", style: "风格", structure: "结构", setting: "设定", continuity: "设定", workflow: "流程", safety: "安全", fact: "事实" })[type] || "未分类"; }
    function ruleMatchesFilter(rule) { const filter = state.ruleFilter || "all"; if (filter === "all") return true; const type = rule?.type || ""; return type === filter || (filter === "setting" && type === "continuity"); }
    function scopeLabel(value) { return ({ draft: "草稿", unreviewed: "未审阅", reviewed: "已审阅", locked: "已锁定", annotated: "已批注", briefed: "有审阅简报", revised: "已修订" })[value] || ruleTypeLabel(value) || value || "全部"; }
    function annotationStatusLabel(status) { return ({ open: "未处理", resolved: "已处理", dismissed: "已忽略" })[status] || status || "未处理"; }
    function patchSourceLabel(source) { const value = String(source || ""); if (value.startsWith("AN-")) return `批注 ${value.slice(3)}`; if (value.startsWith("USER")) return "手动建议"; return value ? "来源批注" : "手动建议"; }
    function ruleDisplayLabel(rule) { const value = String(rule || ""); if (value.startsWith("R-")) return `规则 ${value.slice(2)}`; if (value.startsWith("PB-")) return `导入规则 ${value.slice(3)}`; return value ? "规则" : "无"; }
    function blockDisplayLabel(blockId) { const ids = state.currentChapter?.blockIds || []; const index = ids.indexOf(blockId); if (index >= 0) return `第 ${index + 1} 段`; const match = String(blockId || "").match(/p0*(\d+)/); return match ? `第 ${Number(match[1])} 段` : (blockId ? "当前段落" : "—"); }
    function fileDisplayLabel(file) { const files = Object.keys(state.project?.blocks || {}).sort(); const index = files.indexOf(file); const title = chapterTitle(file); if (index >= 0) return `第 ${index + 1} 章 · ${title}`; const match = String(file || "").match(/ch0*(\d+)/); return match ? `第 ${Number(match[1])} 章 · ${title}` : (title || "当前章节"); }
    function annotationDisplayId(id) { const value = String(id || ""); return value.startsWith("AN-") ? `批注 ${value.slice(3)}` : (value ? "批注" : "批注"); }
    function safetyIssueLabel(code) { return ({ locked_chapter: "目标章节已锁定", reviewed_chapter_requires_secondary_approval: "已审阅章节需要二次确认", reviewed_change_not_marked: "已审阅章节未标注二次确认", hash_mismatch: "段落锚点已变化", unknown_block: "找不到目标段落", unsafe_path: "目标路径不安全", missing_field: "修改建议缺少必要信息", invalid_patch: "修改建议格式不正确", invalid_change: "修改项格式不正确", empty_changes: "没有可应用的修改", unknown_rule: "引用了不存在的规则" })[String(code || "")] || "安全校验未通过"; }
    function diffSafetySummary(file, blockLabel, valid, issues) { if (issues.length) return issues.map((issue, idx) => `安全问题 ${idx + 1}：${safetyIssueLabel(issue.code)}`).join("\\n"); const scope = file ? `${fileDisplayLabel(file)} · ${blockLabel}` : blockLabel; return `安全校验：${valid === false ? "未通过" : "通过"}\\n修改范围：${scope}\\n写入方式：接受后由运行时安全应用`; }
    function statusBadge(text, variant = "") { return `<span class="status-badge ${variant}">${escapeHtml(text)}</span>`; }
    function userErrorMessage(error) { const value = String(error?.message || error || "未知错误"); return /[A-Za-z_][A-Za-z0-9_./:-]*/.test(value) ? "操作失败，请检查输入或稍后重试。" : value; }
    function showError(error) { console.error(error); const message = userErrorMessage(error); toast(message); $("pageSubtitle").innerHTML = `<span class="bad">发生错误：</span>${escapeHtml(message)}`; }
    function setProjectNavEnabled(enabled) { document.querySelectorAll("[data-project-only]").forEach((button) => { button.disabled = !enabled; }); }
    function setView(view) { if (view !== "dashboard" && view !== "settings" && !hasProject()) { toast("请先新建或打开一个项目。"); view = "dashboard"; } if (view === "editor" && hasProject() && !state.currentChapter) { const first = firstChapterFile(); if (first) loadChapter(first).catch(showError); } state.activeView = view; document.querySelectorAll(".view").forEach((el) => el.classList.add("hidden")); const target = $(`view-${view}`); if (target) target.classList.remove("hidden"); document.querySelectorAll(".nav button").forEach((btn) => btn.classList.toggle("active", btn.dataset.view === view)); const titles = { dashboard: hasProject() ? [bookTitle(), "项目总览 · 点击章节进入文稿"] : ["项目", "还没有打开书稿。请从项目列表进入，或新建一个项目。"], editor: ["文稿编辑", `项目 / ${bookTitle()} / 文稿`], annotations: ["批注中心", "集中处理开放批注与智能建议"], discussions: ["项目讨论", "讨论写作意图，不直接改正文"], diff: ["修改差异审核", "所有修改先预览，再由运行时安全应用"], rules: ["规则中心", "规则只传播到草稿 / 未审阅，已锁定章节永不修改"], export: ["导出项目", "本地书稿项目结构可直接复制或进入导出适配器"], settings: ["设置 / 安全中心", "安全边界、智能服务连接与审计策略"] }; const [title, subtitle] = titles[view] || titles.dashboard; $("pageTitle").textContent = title; $("pageSubtitle").textContent = subtitle; if (view === "dashboard") renderDashboard(); if (view === "rules") renderRules(); if (view === "annotations") renderAnnotations(); if (view === "discussions") { renderDiscussions(); loadSidecars().catch(showError); } if (view === "diff") renderDiffIfReady(); }
    function codexStatusLabel(codex) { if (codex?.ok) return "已连接"; if (codex?.error) return `连接失败：${codex.error}`; const labels = { pending_project_open: "打开项目后检测", no_project_open: "打开项目后检测", unavailable: "未连接", timeout: "连接超时" }; return labels[codex?.status] || "未连接"; }
    async function loadHealth() { const health = await api("/api/health"); const codexOk = !!health.codex?.ok; const runtimeOk = !!health.runtime?.ok; const codexLabel = codexStatusLabel(health.codex); $("settingsCodex").textContent = codexLabel; $("settingsCodex").title = codexLabel; $("settingsCodex").className = `status-badge ${codexOk ? "" : "neutral"}`; if (hasProject()) $("pageSubtitle").innerHTML = `运行时 <span class="${runtimeOk ? "ok" : "bad"}">${runtimeOk ? "已连接" : "未打开项目"}</span> · 智能服务 <span class="${codexOk ? "ok" : "bad"}">${escapeHtml(codexLabel)}</span>`; return health; }
    async function loadWorkspace() { const [workspace, projects, project] = await Promise.all([api("/api/workspace"), api("/api/projects"), maybeApi("/api/project")]); state.workspace = workspace; state.projects = projects.projects || []; if (project && project.open !== false) state.project = project; $("settingsWorkspace").textContent = workspace.root ? "本地工作区" : "—"; $("settingsWorkspace").title = workspace.root || ""; $("workspaceShort").textContent = "本地"; $("workspaceBar").style.width = `${Math.min(100, state.projects.length * 12)}%`; renderShell(); if (hasProject()) await loadProjectDetails(false); else renderDashboard(); }
    async function refreshProjects() { const payload = await api("/api/projects"); state.projects = payload.projects || []; renderDashboard(); return state.projects; }
    async function openProject(relativePath, options = {}) { const payload = await api("/api/projects/open", { method: "POST", body: JSON.stringify({ relativePath }) }); state.project = payload.project; state.annotations = payload.project.annotations || []; state.discussions = []; state.audit = []; renderShell(); state.currentFile = null; state.currentChapter = null; state.lastPatch = null; state.lastPreview = null; setView("dashboard"); const sidecars = loadSidecars().catch(showError); if (options.awaitSidecars) await sidecars; loadHealth().catch(() => {}); if (!options.quiet) toast(`已打开：${bookTitle()}`); return state.project; }
    async function loadSidecars() { if (!hasProject()) return null; const [discussionPayload, auditPayload] = await Promise.all([api("/api/discussions"), api("/api/audit")]); state.discussions = discussionPayload.discussions || []; state.audit = auditPayload.events || []; renderShell(); if (state.activeView === "dashboard") renderDashboard(); if (state.activeView === "discussions") renderDiscussions(); return { discussions: state.discussions, audit: state.audit }; }
    async function loadProjectDetails(loadFirst = true) { if (!hasProject()) return null; const [project, discussionPayload, auditPayload] = await Promise.all([api("/api/project"), api("/api/discussions"), api("/api/audit")]); const previousAnnotationId = state.selectedAnnotationId; state.project = project; state.annotations = project.annotations || []; state.discussions = discussionPayload.discussions || []; state.audit = auditPayload.events || []; const openAnnotations = state.annotations.filter((item) => item.status === "open"); state.selectedAnnotationId = openAnnotations.find((item) => item.id === previousAnnotationId)?.id || openAnnotations[0]?.id || null; state.currentFile = state.currentFile || firstChapterFile(); renderShell(); renderDashboard(); renderRules(); if (state.activeView === "annotations") renderAnnotations(); if (state.activeView === "discussions") renderDiscussions(); if (loadFirst && state.currentFile) await loadChapter(state.currentFile); await loadHealth().catch(() => {}); return project; }
    async function loadChapter(file) { if (!file) return null; state.currentFile = file; state.selectionDraft = null; hideSelectionMenu(); state.currentChapter = await api("/api/chapters/" + encodeURIComponent(file)); renderEditor(); return state.currentChapter; }
    function renderShell() { setProjectNavEnabled(hasProject()); $("sideBookTitle").textContent = hasProject() ? bookTitle() : "未打开项目"; $("sideBookMeta").textContent = hasProject() ? `${Object.keys(state.project.blocks || {}).length} 章 · ${state.annotations.length} 条批注 · ${state.discussions.length} 条讨论` : "请选择或新建书稿项目"; }
    function renderDashboard() { const main = $("dashboardMain"); const side = $("dashboardSide"); if (!hasProject()) { const hasProjects = state.projects.length > 0; main.innerHTML = hasProjects ? `<section class="card panel" data-testid="project-list-panel"><div class="panel-title"><div><h2>项目列表</h2><p class="muted">请选择一个已有书稿项目，或新建自己的项目。</p></div><div class="button-row"><button class="ghost-btn" id="refreshProjectListBtn">刷新</button><button class="primary-btn" id="workspaceNewProjectBtn" data-testid="open-new-project-modal">新建项目</button></div></div><div class="project-grid">${state.projects.map(projectCardHtml).join("")}</div></section>` : `<section class="card empty-state" data-testid="empty-workspace"><div><div class="empty-icon">＋</div><h2>还没有书稿项目</h2><p>书稿工作台不会预置任何小说。请新建你自己的项目，或打开工作区中已有的本地书稿项目。</p><button class="primary-btn" id="emptyNewProjectBtn" data-testid="open-new-project-modal">新建项目</button></div></section>`; side.innerHTML = `<section class="card panel"><div class="panel-title"><h3>安全底座</h3></div><div class="check-list"><div class="check-row"><span>▣</span><span>智能输出必须是修改建议</span>${statusBadge("开启")}</div><div class="check-row"><span>🔒</span><span>已锁定章节禁止修改</span>${statusBadge("开启")}</div><div class="check-row"><span>⌘</span><span>接受修改后创建版本记录</span>${statusBadge("开启")}</div></div></section>`; bindProjectListEvents(); $("emptyNewProjectBtn")?.addEventListener("click", openProjectModal); $("workspaceNewProjectBtn")?.addEventListener("click", openProjectModal); return; }
      const files = Object.keys(state.project.blocks || {}).sort(); const summaries = state.project.chapterSummaries || {}; const statuses = state.project.chapterStatus || {}; const open = state.annotations.filter((a) => a.status === "open").length; const locked = Object.values(statuses).filter((s) => s === "locked").length; const rules = state.project.rules || []; const stats = [["📖", "章节", files.length, "真实文件", "purple"], ["字", "总字数", formatNumber(totalWordCount()), "自动统计", "green"], ["🔒", "已锁定", locked, "自动拒绝", "blue"], ["☵", "待处理批注", open, "独立记录", "orange"], ["✦", "活跃规则", rules.length, "先审后改", "purple"], ["✎", "讨论", state.discussions.length, "独立记录", "blue"]]; main.innerHTML = `<div class="stats">${stats.map(([icon, label, num, note, color]) => `<div class="card metric"><div class="metric-icon ${color}">${icon}</div><div class="metric-body"><span class="label">${label}</span><strong class="num">${num}</strong><span class="note muted">${note}</span></div></div>`).join("")}</div><section class="card panel"><div class="panel-title"><h2>章节列表</h2><button class="ghost-btn" id="refreshProjectBtn">刷新</button></div><div class="table-scroll"><table class="chapter-table"><thead><tr><th>章节</th><th>状态</th><th>块数</th><th>字数</th><th></th></tr></thead><tbody id="chapterRows">${files.map((file, idx) => { const summary = summaries[file] || {}; const blocks = state.project.blocks[file] || []; const title = summary.title || titleFromPath(file); const status = summary.status || statuses[file] || "draft"; const words = summary.wordCount ?? wordCount(Object.values(blocks).map((b) => b.text || "").join("\\n")); return `<tr data-file="${escapeHtml(file)}" title="打开章节"><td><strong>${idx + 1}. ${escapeHtml(title)}</strong><br><span class="muted">本地章节文件</span></td><td><span class="status-pill ${statusClass(status)}">${statusLabel(status)}</span></td><td>${summary.blockCount || blocks.length}</td><td class="chapter-words">${formatNumber(words)}</td><td><span class="muted">打开</span></td></tr>`; }).join("")}</tbody></table></div></section>`; const workflow = powerbookWorkflowHtml(); side.innerHTML = `<section class="card panel"><div class="panel-title"><h3>快捷操作</h3></div><div class="button-row single"><button class="primary-btn" id="newProjectBtn">⊕ 新建项目</button><button class="ghost-btn" id="openEditorBtn">▤ 打开文稿</button><button class="ghost-btn" id="newAnnotationBtn">☵ 处理批注</button><button class="ghost-btn" id="newDiscussionBtn">✎ 新建讨论</button></div></section>${workflow}<section class="card panel"><div class="panel-title"><h3>项目列表</h3><button class="link-btn" id="refreshProjectListBtn">刷新</button></div><div class="project-grid" style="grid-template-columns:1fr">${state.projects.map(projectCardHtml).join("")}</div></section><section class="card panel"><div class="panel-title"><h3>智能建议</h3></div><div id="aiSuggestions">${open ? `有 ${open} 条批注可处理。` : "当前没有待处理批注。"}</div></section><section class="card panel"><div class="panel-title"><h3>项目健康度</h3></div><div class="check-list"><div class="check-row"><span>▣</span><span>运行时</span>${statusBadge("正常")}</div><div class="check-row"><span>⌘</span><span>版本记录</span>${statusBadge("接受修改时创建")}</div></div></section>`; document.querySelectorAll("#chapterRows tr").forEach((tr) => tr.addEventListener("click", () => loadChapter(tr.dataset.file).then(() => setView("editor")).catch(showError))); $("refreshProjectBtn")?.addEventListener("click", () => loadProjectDetails(false).catch(showError)); $("newProjectBtn")?.addEventListener("click", openProjectModal); $("openEditorBtn")?.addEventListener("click", () => { const first = firstChapterFile(); if (first) loadChapter(first).then(() => setView("editor")).catch(showError); }); $("newAnnotationBtn")?.addEventListener("click", () => setView("annotations")); $("newDiscussionBtn")?.addEventListener("click", () => setView("discussions")); bindProjectListEvents(); }
    function powerbookWorkflowHtml() { const workflow = state.project?.powerbookWorkflow; if (!workflow) return ""; const counts = workflow.statusCounts || {}; const countHtml = Object.entries(counts).length ? Object.entries(counts).map(([key, value]) => `<div class="check-row"><span>•</span><span>${escapeHtml(statusLabel(key))}</span><strong>${escapeHtml(value)}</strong></div>`).join("") : `<div class="check-row"><span>•</span><span>导入状态</span><strong>已保留</strong></div>`; const artifacts = workflow.artifacts || []; const artifactHtml = artifacts.length ? artifacts.map((item) => `<div class="check-row"><span>📎</span><span>${escapeHtml(item.label)}<br><span class="muted">已放入项目资料</span></span><strong>已导入</strong></div>`).join("") : `<div class="check-row"><span>📎</span><span>审阅资料</span><strong>暂无</strong></div>`; return `<section class="card panel"><div class="panel-title"><h3>导入工作流</h3></div><div class="check-list">${countHtml}</div><h3 class="mt">审阅资料</h3><div class="check-list">${artifactHtml}</div></section>`; }
    function projectCardHtml(project) { return `<button class="project-card" data-project="${escapeHtml(project.relativePath)}" data-testid="project-card" title="打开项目"><span class="tag">本地项目</span><strong>${escapeHtml(project.title || "未命名作品")}</strong><div class="meta">${project.chapterCount || 0} 章 · ${project.annotationCount || 0} 条批注<br>点击打开项目</div></button>`; }
    function bindProjectListEvents() { document.querySelectorAll("[data-project]").forEach((card) => card.addEventListener("click", () => openProject(card.dataset.project).catch(showError))); $("refreshProjectListBtn")?.addEventListener("click", () => refreshProjects().catch(showError)); }
    function renderEditor() { const chapter = state.currentChapter; if (!chapter) return; const blocks = chapter.blocks || {}; const ids = chapter.blockIds || Object.keys(blocks); const title = chapter.title || titleFromPath(chapter.file); const allText = Object.values(blocks).map((b) => b.text).join("\\n"); const words = wordCount(allText); $("chapterSelect").textContent = title; $("editorStatus").textContent = statusLabel(chapter.status); $("editorStatus").className = `status-pill ${statusClass(chapter.status)}`; $("editorWords").textContent = `字数：${words.toLocaleString()}`; $("chapterWordCount").textContent = `本章字数：${words.toLocaleString()}`; $("docView").innerHTML = `<h2>${escapeHtml(title)}</h2>` + ids.map((id) => { const block = blocks[id]; const annotation = blockAnnotation(id); return `<div class="paragraph ${annotation ? "annotated" : ""}" data-block="${escapeHtml(id)}"><div class="pid">${escapeHtml(blockDisplayLabel(id))}</div><div class="ptext" data-testid="block-text">${escapeHtml(block.text)}</div><div><button class="ghost-btn add-annotation-btn" data-block="${escapeHtml(id)}">添加批注</button>${annotation ? `<br><span class="tag">${escapeHtml(annotationDisplayId(annotation.id))}</span>` : ""}</div></div>`; }).join(""); document.querySelectorAll(".add-annotation-btn").forEach((btn) => btn.addEventListener("click", () => openAnnotationModal(btn.dataset.block))); renderAnnotationPanel(); renderDiffIfReady(); }
    function renderAnnotationPanel() { const list = state.annotations.filter((item) => !state.currentFile || item.file === state.currentFile); const openCount = list.filter((item) => item.status === "open").length; if (state.selectedAnnotationId && !list.some((item) => item.id === state.selectedAnnotationId && item.status === "open")) state.selectedAnnotationId = list.find((item) => item.status === "open")?.id || null; $("annotationCount").textContent = list.length; $("pendingAnnotationCount").textContent = openCount; $("reviseCurrentBtn").disabled = openCount === 0; $("generateSuggestionBtn").disabled = openCount === 0; document.querySelectorAll("[data-annotation-tab]").forEach((tab) => tab.classList.toggle("active", tab.dataset.annotationTab === state.activeAnnotationTab)); if (state.activeAnnotationTab === "suggestions") { $("annotationPanel").innerHTML = `<div class="ai-item"><span class="small-icon purple">✦</span><div><strong>${openCount ? `${openCount} 条批注可处理` : "暂无待处理批注"}</strong><br><span class="muted">点击“处理当前批注”后只生成修改建议。</span></div><span class="tag">建议</span></div><div class="ai-item"><span class="small-icon blue">▣</span><div><strong>安全边界</strong><br><span class="muted">用户接受前正文不写入；锚点漂移会被拒绝。</span></div><span class="tag">运行时</span></div>`; } else { $("annotationPanel").innerHTML = list.length ? list.map((a) => { const isOpen = a.status === "open"; return `<button class="annotation-card ${isOpen && a.id === state.selectedAnnotationId ? "active" : ""}" data-annotation="${escapeHtml(a.id)}" ${isOpen ? "" : "disabled"}><div class="annotation-head"><strong>${escapeHtml(annotationDisplayId(a.id))}</strong><span>${escapeHtml(fileDisplayLabel(a.file))} · ${escapeHtml(blockDisplayLabel(a.block_id))}</span></div><p>${escapeHtml(a.text)}</p><div class="annotation-head"><span>${escapeHtml(annotationStatusLabel(a.status))}</span><span>${isOpen ? "选择处理 ›" : "已闭环"}</span></div></button>`; }).join("") : `<div class="empty-state" style="min-height:180px"><div><div class="empty-icon">☵</div><h2>当前章节暂无批注</h2><p>选中文稿中的句子，点击“添加批注”。批注会保存在独立记录中，不污染正文。</p></div></div>`; } document.querySelectorAll("[data-annotation]").forEach((el) => el.addEventListener("click", () => { if (el.disabled) return; state.selectedAnnotationId = el.dataset.annotation; state.activeAnnotationTab = "annotations"; renderAnnotationPanel(); })); $("aiActionList").innerHTML = `<div class="ai-item"><span class="small-icon purple">✦</span><div><strong>${openCount ? "局部改写" : "暂无待处理批注"}</strong><br><span class="muted">${openCount ? "只生成修改建议，用户接受前不写正文" : "已处理批注不会再次生成建议"}</span></div><span class="tag">安全</span></div><div class="ai-item"><span class="small-icon blue">▣</span><div><strong>锚点校验</strong><br><span class="muted">锚点校验不匹配时拒绝自动应用</span></div><span class="tag">强制</span></div>`; }
    function renderAnnotationCards(list, max = 80) { const visible = list.slice(0, max); const omitted = Math.max(0, list.length - visible.length); const note = omitted ? `<div class="annotation-list-note">已显示前 ${visible.length} / ${list.length} 条批注，打开具体章节可继续缩小范围。</div>` : ""; return note + visible.map((a) => `<div class="annotation-card"><div class="annotation-head"><strong>${escapeHtml(annotationDisplayId(a.id))}</strong><span>${escapeHtml(fileDisplayLabel(a.file))} · ${escapeHtml(blockDisplayLabel(a.block_id))}</span></div><p>${escapeHtml(a.text)}</p><span class="status-pill status-${a.status === "open" ? "draft" : "reviewed"}">${escapeHtml(annotationStatusLabel(a.status))}</span></div>`).join(""); }
    function renderAnnotations() { $("annotationCenter").innerHTML = state.annotations.length ? renderAnnotationCards(state.annotations, 80) : `<div class="empty-state" style="min-height:260px"><div><div class="empty-icon">☵</div><h2>暂无批注</h2><p>批注会保存在独立文件中，不会插入正文。</p></div></div>`; }
    function renderDiscussions() { const list = state.discussions || []; $("discussionCount").textContent = list.length; $("discussionList").innerHTML = list.length ? list.map((item) => `<div class="discussion-card" data-testid="discussion-card"><div class="annotation-head"><strong>讨论记录</strong><span>${escapeHtml(item.file ? fileDisplayLabel(item.file) : "项目")}${item.blockId ? " · " + escapeHtml(blockDisplayLabel(item.blockId)) : ""}</span></div><p>${escapeHtml(item.text)}</p></div>`).join("") : `<div class="empty-state" style="min-height:220px"><div><div class="empty-icon">✎</div><h2>暂无讨论</h2><p>先记录写作意图；后续改稿仍要先审核再写入。</p></div></div>`; }
    function renderRules() { if (!hasProject()) return; const rules = state.project?.rules || []; const filtered = rules.filter(ruleMatchesFilter); if (!state.selectedRuleId || !rules.find((rule) => rule.id === state.selectedRuleId)) state.selectedRuleId = filtered[0]?.id || rules[0]?.id || null; if (state.selectedRuleId && !filtered.find((rule) => rule.id === state.selectedRuleId)) state.selectedRuleId = filtered[0]?.id || null; $("ruleCount").textContent = rules.length; document.querySelectorAll("[data-rule-filter]").forEach((el) => el.classList.toggle("active", el.dataset.ruleFilter === state.ruleFilter)); const summary = state.ruleFilter === "all" ? `显示全部 ${rules.length} 条规则` : `显示 ${ruleTypeLabel(state.ruleFilter)}：${filtered.length} / ${rules.length} 条`; $("ruleFilterSummary").textContent = summary; $("ruleList").innerHTML = filtered.length ? filtered.map((rule) => `<button class="rule-row ${rule.id === state.selectedRuleId ? "active" : ""}" data-rule-id="${escapeHtml(rule.id)}"><div class="rule-meta"><span>${escapeHtml(ruleDisplayLabel(rule.id))} · ${escapeHtml(ruleTypeLabel(rule.type))}</span>${statusBadge("启用")}</div><strong>${escapeHtml(rule.text)}</strong><div class="rule-meta"><span>来源：${escapeHtml((rule.source_annotations || []).map(annotationDisplayId).join("、") || "用户规则")}</span><span class="tag">${escapeHtml((rule.apply_to || []).map(scopeLabel).join(" / ") || "全部")}</span></div></button>`).join("") : `<div class="empty-state" style="min-height:220px"><div><div class="empty-icon">▱</div><h2>没有匹配的规则</h2><p>当前筛选为 ${escapeHtml(ruleTypeLabel(state.ruleFilter))}，可切回“全部”。</p></div></div>`; document.querySelectorAll("[data-rule-id]").forEach((row) => row.addEventListener("click", () => { state.selectedRuleId = row.dataset.ruleId; renderRules(); })); const rule = rules.find((item) => item.id === state.selectedRuleId) || filtered[0]; $("ruleDetail").innerHTML = rule ? `<div><strong>${escapeHtml(ruleDisplayLabel(rule.id))}</strong> ${statusBadge("启用")}</div><h2>${escapeHtml(rule.text)}</h2><p class="muted">类型：${escapeHtml(ruleTypeLabel(rule.type))}</p><p class="muted">作用范围：${escapeHtml((rule.apply_to || []).map(scopeLabel).join(" / ") || "全部")}；排除：${escapeHtml((rule.exclude || []).map(scopeLabel).join(" / ") || "无")}</p>` : `<h2>选择一条规则</h2><p class="muted">规则传播必须先生成修改建议。</p>`; }
    async function runRevise() { const annotation = selectedAnnotation(); if (!annotation) throw new Error("当前章节没有待处理批注。请先添加批注，或重新定位已有批注。 "); const chapterBefore = state.currentFile ? await api("/api/chapters/" + encodeURIComponent(state.currentFile)) : null; toast("正在调用智能服务生成修改建议，超时后会安全回退到本地运行时。"); const result = await api("/api/ai/revise", { method: "POST", body: JSON.stringify({ annotationIds: [annotation.id], file: annotation.file, timeoutSeconds: 30 }) }); state.lastPatch = result.output; if (chapterBefore && annotation.file === state.currentFile) state.currentChapter = chapterBefore; const sourceLabel = result.source === "codex-app-server" ? "智能服务已生成修改建议" : "智能服务不可用或未通过校验，已回退到本地安全建议"; toast(`${sourceLabel}，进入差异审核。`); await previewPatch(false); setView("diff"); return result; }
    async function previewPatch(showToast = true) { if (!state.lastPatch) await runRevise(); state.lastPreview = await api("/api/patch/preview", { method: "POST", body: JSON.stringify({ patch: state.lastPatch }) }); renderDiffIfReady(); if (showToast) toast("差异预览已生成。 "); return state.lastPreview; }
    async function applyPatch() { if (!state.lastPatch) await runRevise(); if (state.lastPreview && state.lastPreview.validation && state.lastPreview.validation.valid === false) { toast("当前建议未通过校验，不能提交。请重新定位批注或重新生成建议。"); renderDiffIfReady(); return { applied: false, validation: state.lastPreview.validation }; } const result = await api("/api/patch/apply", { method: "POST", body: JSON.stringify({ patch: state.lastPatch }) }); if (!result.applied) { state.lastPreview = { validation: result.validation, diff: "" }; renderDiffIfReady(); throw new Error("修改建议未通过校验，未应用。 "); } toast(result.commitError ? "修改已应用，但版本记录被跳过。" : "接受并提交成功。 "); const current = state.currentFile; state.lastPatch = null; state.lastPreview = null; await loadProjectDetails(false); if (current) await loadChapter(current); setView("editor"); return result; }
    async function manualReviseCurrentBlock() { const chapter = state.currentChapter; if (!chapter) throw new Error("尚未加载章节。 "); const blockId = (chapter.blockIds || [])[0]; const block = chapter.blocks[blockId]; const suffix = block.text.trim() ? "\\n他停了一下，把手里的物件放回原处。" : "他停了一下，把手里的物件放回原处。"; const patch = await api("/api/patch/manual", { method: "POST", body: JSON.stringify({ file: chapter.file, blockId, afterText: `${block.text}${suffix}`, reason: "手动生成修改建议" }) }); state.lastPatch = patch; await previewPatch(false); setView("diff"); toast("已为当前段落生成手动修改建议。 "); }
    function renderDiffIfReady() { const patch = state.lastPatch; if (!patch) { $("rawDiff").textContent = ""; $("acceptPatchBtn").disabled = true; $("invalidPatchActions").classList.add("hidden"); return; } const change = patch.changes?.[0] || {}; const file = change.file || state.currentFile || ""; const blockId = change.targetBlockId || "—"; const source = (patch.sourceAnnotations || [])[0] || "USER"; const displayBlock = blockDisplayLabel(blockId); const before = state.currentChapter?.blocks?.[blockId]?.text || ""; const after = change.afterText || ""; const valid = state.lastPreview?.validation?.valid ?? patch.validation?.valid; const issues = state.lastPreview?.validation?.issues || patch.validation?.issues || []; const isRejected = valid === false || issues.length > 0; const needsRemap = issues.some((issue) => issue.code === "hash_mismatch"); $("diffTitle").textContent = file ? `${fileDisplayLabel(file)} · 修改差异审核` : "修改差异审核"; ["diffBlockTag", "beforeBlockTag", "afterBlockTag", "impactBlock"].forEach((id) => $(id).textContent = displayBlock); $("sourceAnnotation").textContent = patchSourceLabel(source); $("rulesUsed").textContent = ruleDisplayLabel((patch.rulesUsed || [])[0]); $("patchValidity").textContent = valid === undefined ? "待预览" : (valid ? "通过" : "拒绝"); $("patchValidity").className = valid ? "ok" : "bad"; $("acceptPatchBtn").disabled = isRejected || valid === undefined; $("acceptPatchBtn").textContent = isRejected ? "无法提交" : "接受并提交"; $("invalidPatchActions").classList.toggle("hidden", !isRejected); $("remapAnnotationBtn").classList.toggle("hidden", !needsRemap); $("changeReason").textContent = issues.length ? (needsRemap ? "校验未通过：批注锚点已经变化。请先重新定位批注，或重新生成修改建议。" : "校验未通过：请重新生成修改建议或检查章节状态。") : (change.reason || patch.summary || "根据批注生成建议修改。"); $("rawDiff").textContent = diffSafetySummary(file, displayBlock, valid, issues); $("diffReasonCard").classList.toggle("collapsed", !!state.diffReasonCollapsed); $("toggleDiffReasonBtn").textContent = state.diffReasonCollapsed ? "展开" : "收起"; $("toggleDiffReasonBtn").setAttribute("aria-expanded", String(!state.diffReasonCollapsed)); $("beforeLines").innerHTML = splitLines(before).map((line, idx) => `<div class="diff-line minus"><span class="ln">${idx + 1}</span><span>${escapeHtml(line)}</span><span>−</span></div>`).join(""); $("afterLines").innerHTML = splitLines(after).map((line, idx) => `<div class="diff-line plus"><span class="ln">${idx + 1}</span><span>${escapeHtml(line)}</span><span>＋</span></div>`).join(""); $("changeCheckList").innerHTML = issues.length ? issues.map((issue) => `<div class="check-row"><span>!</span><span>${escapeHtml(safetyIssueLabel(issue.code))}</span><strong class="bad">拒绝</strong></div>`).join("") : `<div class="check-row"><span>▤</span><span>修改的块<br><span class="muted">${escapeHtml(displayBlock)}</span></span>${statusBadge("通过")}</div><div class="check-row"><span>✦</span><span>将应用的规则</span><strong class="ok">${escapeHtml(ruleDisplayLabel((patch.rulesUsed || [])[0]))}</strong></div><div class="check-row"><span>🔒</span><span>锁定章节保持不变</span>${statusBadge("是")}</div>`; $("commitPreview").textContent = isRejected ? "当前建议未通过运行时校验，不能写入或提交。" : `安全应用修改建议：${patchSourceLabel(source)}`; }
    function openProjectModal() { $("projectForm").reset(); $("projectModal").classList.remove("hidden"); setTimeout(() => $("projectTitleInput").focus(), 0); }
    function closeProjectModal() { $("projectModal").classList.add("hidden"); }
    async function submitProject(event) { event.preventDefault(); const payload = Object.fromEntries(new FormData(event.target).entries()); const result = await api("/api/projects/create", { method: "POST", body: JSON.stringify(payload) }); closeProjectModal(); await refreshProjects(); const relativePath = result.summary?.relativePath || result.plan?.slug || payload.slug; if (relativePath) { await openProject(relativePath, { quiet: true, awaitSidecars: true }); toast(`已创建并打开项目：${result.summary?.title || result.plan?.title || payload.title}`); } else { toast(`已创建项目：${result.summary?.title || result.plan?.title || payload.title}`); } }
    function selectionContext() { const selection = window.getSelection(); const text = selection?.toString().trim() || ""; if (!text || !selection.rangeCount) return null; const anchor = selection.anchorNode?.nodeType === Node.ELEMENT_NODE ? selection.anchorNode : selection.anchorNode?.parentElement; const focus = selection.focusNode?.nodeType === Node.ELEMENT_NODE ? selection.focusNode : selection.focusNode?.parentElement; const anchorParagraph = anchor?.closest?.(".paragraph[data-block]"); const focusParagraph = focus?.closest?.(".paragraph[data-block]"); if (!anchorParagraph || !focusParagraph || anchorParagraph !== focusParagraph) return null; const blockId = anchorParagraph.dataset.block; const block = state.currentChapter?.blocks?.[blockId]; if (!block || !block.text.includes(text)) return null; const rect = selection.getRangeAt(0).getBoundingClientRect(); return { blockId, selectedText: text, rect }; }
    function selectedBlockIdFromSelection() { return selectionContext()?.blockId || null; }
    function hideSelectionMenu() { $("selectionMenu").classList.add("hidden"); }
    function showSelectionMenu(context, point) { if (!context) { hideSelectionMenu(); return; } state.selectionDraft = { blockId: context.blockId, selectedText: context.selectedText }; $("selectionPreview").textContent = context.selectedText; $("selectionInfo").textContent = `已选中 ${context.selectedText.length} 字，可右键或双击添加批注`; const menu = $("selectionMenu"); menu.classList.remove("hidden"); const rect = context.rect || { left: point?.x || 0, bottom: point?.y || 0, top: point?.y || 0 }; const left = Math.min(window.innerWidth - menu.offsetWidth - 12, Math.max(12, point?.x ?? rect.left)); const top = Math.min(window.innerHeight - menu.offsetHeight - 12, Math.max(12, (point?.y ?? rect.bottom) + 10)); menu.style.left = `${left}px`; menu.style.top = `${top}px`; }
    function updateSelectionMenu(point) { const context = selectionContext(); if (!context) { state.selectionDraft = null; $("selectionInfo").textContent = "可选中文本后添加批注"; hideSelectionMenu(); return null; } showSelectionMenu(context, point); return context; }
    function openAnnotationModal(blockId, selectedText) { if (!state.currentChapter) { toast("请先打开一个章节，再添加批注。"); return; } const draft = state.selectionDraft; const explicitBlockId = blockId || draft?.blockId || selectedBlockIdFromSelection(); if (!explicitBlockId) { toast("请先在当前章节中选择一段文字，再右键、双击或点击“添加批注”。"); return; } const block = state.currentChapter.blocks[explicitBlockId]; if (!block) { toast("未找到可批注的段落，请重新选择。"); return; } const selectionText = selectedText || (draft?.blockId === explicitBlockId ? draft.selectedText : "") || window.getSelection()?.toString().trim(); $("annotationFileInput").value = state.currentChapter.file; $("annotationBlockInput").value = block.id; $("annotationSelectedInput").value = selectionText && block.text.includes(selectionText) ? selectionText : block.text; $("annotationBodyInput").value = ""; hideSelectionMenu(); $("annotationModal").classList.remove("hidden"); setTimeout(() => $("annotationBodyInput").focus(), 0); }
    function openAnnotationFromSelection() { const current = selectionContext(); const context = current || (state.selectionDraft ? { blockId: state.selectionDraft.blockId, selectedText: state.selectionDraft.selectedText } : null); if (!context) { toast("请先选中文稿中的一段文字。"); return; } openAnnotationModal(context.blockId, context.selectedText); }
    function closeAnnotationModal() { $("annotationModal").classList.add("hidden"); }
    async function submitAnnotation(event) { event.preventDefault(); const payload = { file: $("annotationFileInput").value, blockId: $("annotationBlockInput").value, selectedText: $("annotationSelectedInput").value, text: $("annotationBodyInput").value, type: "style", priority: "high" }; const result = await api("/api/annotations/create", { method: "POST", body: JSON.stringify(payload) }); closeAnnotationModal(); await loadProjectDetails(false); if (state.currentFile) await loadChapter(state.currentFile); state.selectedAnnotationId = result.annotation.id; toast("批注已写入独立文件，正文未被污染。 "); }
    async function submitDiscussion(event) { event.preventDefault(); const payload = { text: $("discussionTextInput").value, file: state.currentFile || "", blockId: state.currentChapter?.blockIds?.[0] || "" }; const result = await api("/api/discussions/create", { method: "POST", body: JSON.stringify(payload) }); state.discussions = [result.discussion, ...(state.discussions || [])]; $("discussionTextInput").value = ""; renderDiscussions(); toast("讨论已写入独立文件，正文未被修改。 "); await loadProjectDetails(false); setView("discussions"); }
    function bindEvents() { document.querySelectorAll(".nav button").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view))); $("closeProjectModalBtn").addEventListener("click", closeProjectModal); $("cancelProjectBtn").addEventListener("click", closeProjectModal); $("projectForm").addEventListener("submit", (event) => submitProject(event).catch(showError)); $("closeAnnotationModalBtn").addEventListener("click", closeAnnotationModal); $("cancelAnnotationBtn").addEventListener("click", closeAnnotationModal); $("annotationForm").addEventListener("submit", (event) => submitAnnotation(event).catch(showError)); $("discussionForm").addEventListener("submit", (event) => submitDiscussion(event).catch(showError)); $("saveBtn").addEventListener("click", () => toast("本地书稿项目已保存在文件中。")); $("generateSuggestionBtn").addEventListener("click", () => runRevise().catch(showError)); $("reviseCurrentBtn").addEventListener("click", () => runRevise().catch(showError)); $("manualPatchBtn").addEventListener("click", () => manualReviseCurrentBlock().catch(showError)); $("addAnnotationGlobalBtn").addEventListener("click", () => openAnnotationModal()); $("selectionAddAnnotationBtn").addEventListener("click", openAnnotationFromSelection); $("docView").addEventListener("mouseup", (event) => setTimeout(() => updateSelectionMenu({ x: event.clientX, y: event.clientY }), 0)); $("docView").addEventListener("keyup", () => updateSelectionMenu()); $("docView").addEventListener("contextmenu", (event) => { const context = updateSelectionMenu({ x: event.clientX, y: event.clientY }); if (context) event.preventDefault(); }); $("docView").addEventListener("dblclick", (event) => { const current = selectionContext(); const draft = state.selectionDraft ? { blockId: state.selectionDraft.blockId, selectedText: state.selectionDraft.selectedText, rect: current?.rect } : null; const context = draft && draft.selectedText.length >= (current?.selectedText.length || 0) ? draft : current; if (context) openAnnotationModal(context.blockId, context.selectedText); }); document.addEventListener("click", (event) => { if (!$("selectionMenu").contains(event.target) && !$("docView").contains(event.target)) hideSelectionMenu(); }); document.querySelectorAll("[data-annotation-tab]").forEach((tab) => tab.addEventListener("click", () => { state.activeAnnotationTab = tab.dataset.annotationTab; renderAnnotationPanel(); })); $("addRuleBtn").addEventListener("click", () => setView("rules")); $("laterBtn").addEventListener("click", () => toast("已加入稍后处理列表。")); $("backToEditorBtn").addEventListener("click", () => setView("editor")); $("refreshDiffBtn").addEventListener("click", () => previewPatch().catch(showError)); $("toggleDiffReasonBtn").addEventListener("click", () => { state.diffReasonCollapsed = !state.diffReasonCollapsed; renderDiffIfReady(); }); $("rejectPatchBtn").addEventListener("click", () => { state.lastPatch = null; state.lastPreview = null; toast("已拒绝当前建议，未写入文件。 "); setView("editor"); }); $("partialPatchBtn").addEventListener("click", () => toast("局部应用将在下一阶段支持；当前可接受完整安全修改。")); $("acceptPatchBtn").addEventListener("click", () => { if ($("acceptPatchBtn").disabled) return; applyPatch().catch(showError); }); $("regeneratePatchBtn").addEventListener("click", () => runRevise().catch(showError)); $("remapAnnotationBtn").addEventListener("click", () => { toast("请回到文稿重新选择文字并添加批注，旧建议不会自动写入。"); setView("editor"); }); $("newRuleBtn").addEventListener("click", () => toast("规则创建入口已预留，当前规则由批注提炼生成。")); $("ruleFilterBtn").addEventListener("click", () => { const panel = $("ruleFilterPanel"); panel.classList.toggle("hidden"); $("ruleFilterBtn").setAttribute("aria-expanded", String(!panel.classList.contains("hidden"))); }); document.querySelectorAll("[data-rule-filter]").forEach((control) => control.addEventListener("click", () => { state.ruleFilter = control.dataset.ruleFilter || "all"; renderRules(); })); $("batchApplyBtn").addEventListener("click", () => toast("规则传播会生成修改建议，当前演示保持预览状态。")); $("previewRuleImpactBtn").addEventListener("click", () => toast("已生成规则传播影响预览：仅草稿 / 未审阅章节。")); $("annotationToEditorBtn").addEventListener("click", () => setView("editor")); $("copyProjectPathBtn").addEventListener("click", async () => { const root = state.project?.root || ""; $("projectPathBox").textContent = root; try { await navigator.clipboard.writeText(root); toast("项目路径已复制。 "); } catch (_) { toast("项目路径已显示。 "); } }); document.addEventListener("keydown", (event) => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); document.querySelector(".search input")?.focus(); } }); window.BookWorkbench = { api, state, loadWorkspace, loadProjectDetails, loadSidecars, openProject, runRevise, previewPatch, applyPatch, manualReviseCurrentBlock, submitDiscussion, setView, updateSelectionMenu, openAnnotationFromSelection } }
    async function boot() { bindEvents(); await loadWorkspace(); setView("dashboard"); loadHealth().catch(() => { $("settingsCodex").textContent = "未连接"; $("settingsCodex").className = "status-badge neutral"; }); }
    boot().catch(showError);
  </script>
</body>
</html>
"""


class RuntimeWebApp:
    def __init__(
        self,
        project_root: str | Path | None = None,
        *,
        workspace_root: str | Path | None = None,
        builtin_skills_root: str | Path | None = None,
        codex_client: CodexAppServerClient | None = None,
    ) -> None:
        if project_root is None and workspace_root is None:
            raise ValueError("Either project_root or workspace_root is required.")
        self.project_root = Path(project_root).resolve() if project_root is not None else None
        self.workspace_root = (
            Path(workspace_root).resolve()
            if workspace_root is not None
            else self.project_root.parent  # type: ignore[union-attr]
        )
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.builtin_skills_root = builtin_skills_root
        self.runtime = RuntimeOrchestrator(self.project_root, builtin_skills_root=builtin_skills_root) if self.project_root is not None else None
        self.codex_client = codex_client or CodexAppServerClient(cwd=self.project_root or self.workspace_root)
        self._lock = threading.RLock()

    def _require_runtime(self) -> RuntimeOrchestrator:
        if self.runtime is None or self.project_root is None:
            raise ProjectLoadError("No project is open. Create or open a project first.")
        return self.runtime

    def _load_context(self):
        if self.project_root is None:
            raise ProjectLoadError("No project is open. Create or open a project first.")
        return load_project(self.project_root)

    def workspace(self) -> Dict[str, Any]:
        return {"root": self.workspace_root.as_posix(), "projectCount": len(list_projects(self.workspace_root))}

    def projects(self) -> Dict[str, Any]:
        return {"workspaceRoot": self.workspace_root.as_posix(), "projects": list_projects(self.workspace_root)}

    def open_project(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        relative_path = _required_string(payload, "relativePath")
        project_root = resolve_workspace_project(self.workspace_root, relative_path)
        with self._lock:
            self.project_root = project_root
            self.runtime = RuntimeOrchestrator(project_root, builtin_skills_root=self.builtin_skills_root)
            if isinstance(self.codex_client, CodexAppServerClient):
                self.codex_client.cwd = project_root
            project = self.runtime.inspect()
            context = self.runtime.refreshed_context()
            project["chapterSummaries"] = chapter_summaries(context)
            project["powerbookWorkflow"] = _powerbook_workflow_summary(context.root)
        project["open"] = True
        project["summary"] = project_summary(project_root, self.workspace_root)
        return {"project": project}

    def health(self) -> Dict[str, Any]:
        with self._lock:
            if self.runtime is not None:
                project = self.runtime.inspect()
                runtime = {
                    "ok": True,
                    "projectRoot": project["root"],
                    "chapters": len(project["blocks"]),
                    "annotations": len(project["annotations"]),
                    "skills": sorted(project["skills"]),
                }
            else:
                runtime = {"ok": False, "reason": "no_project_open"}
        codex = self.codex_client.health() if self.project_root is not None else {
            "ok": False,
            "status": "pending_project_open",
            "command": getattr(self.codex_client, "command", ["codex", "app-server"]),
            "error": None,
            "notifications": [],
            "durationMs": 0,
        }
        return {
            "app": {"name": APP_TITLE, "ok": True},
            "workspace": self.workspace(),
            "runtime": runtime,
            "codex": codex,
        }

    def project(self) -> Dict[str, Any]:
        with self._lock:
            if self.runtime is None:
                return {"open": False, "workspaceRoot": self.workspace_root.as_posix()}
            project = self.runtime.inspect()
            context = self.runtime.refreshed_context()
            project["chapterSummaries"] = chapter_summaries(context)
            project["powerbookWorkflow"] = _powerbook_workflow_summary(context.root)
            project["open"] = True
            project["summary"] = project_summary(self.project_root, self.workspace_root)  # type: ignore[arg-type]
            return project

    def discussions(self) -> Dict[str, Any]:
        context = self._load_context()
        return {"discussions": list(reversed(list_discussions(context.root)))}

    def create_discussion(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        context = self._load_context()
        file_path = _optional_string(payload, "file") or ""
        block_id = _optional_string(payload, "blockId") or ""
        if file_path:
            safe_chapter_path(context.root, file_path)
            if block_id:
                context.block(file_path, block_id)
        discussion = append_discussion(
            context.root,
            text=_required_string(payload, "text"),
            file_path=file_path,
            block_id=block_id,
            role=_optional_string(payload, "role") or "author",
        )
        AuditLog(context.root).append({"type": "discussion.created", "discussionId": discussion["id"], "file": file_path, "blockId": block_id})
        return {"discussion": discussion}

    def annotations(self, query: Mapping[str, list[str]]) -> Dict[str, Any]:
        context = self._load_context()
        annotations = list_annotations(
            context,
            file_path=_first(query, "file"),
            status=_first(query, "status"),
            annotation_type=_first(query, "type"),
            include_resolved=_bool_query(query, "include_resolved") or _bool_query(query, "includeResolved"),
        )
        return {
            "annotations": [annotation_to_dict(item) for item in annotations],
            "summary": classification_summary(annotations),
        }

    def create_annotation(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        context = self._load_context()
        file_path = _required_string(payload, "file")
        block_id = _required_string(payload, "blockId")
        selected_text = _required_string(payload, "selectedText")
        text = _required_string(payload, "text")
        block = context.block(file_path, block_id)
        if selected_text not in block.text:
            raise ValueError("selectedText must be contained in the target block.")
        start = block.text.index(selected_text)
        annotation_id = self._next_annotation_id(context)
        annotation = {
            "id": annotation_id,
            "file": file_path,
            "target": {
                "blockId": block_id,
                "selectedText": selected_text,
                "beforeHash": block.before_hash,
                "startOffset": start,
                "endOffset": start + len(selected_text),
                "confidence": 1.0,
            },
            "body": {
                "text": text,
                "type": _optional_string(payload, "type") or "style",
                "priority": _optional_string(payload, "priority") or "medium",
            },
            "metadata": {
                "author": _optional_string(payload, "author") or "local-user",
                "status": "open",
            },
        }
        annotations_path = context.root / ".bookai" / "annotations.jsonl"
        annotations_path.parent.mkdir(parents=True, exist_ok=True)
        with annotations_path.open("a", encoding="utf-8") as handle:
            if annotations_path.exists() and annotations_path.stat().st_size > 0:
                handle.write("\n")
            handle.write(json.dumps(annotation, ensure_ascii=False))
        # Keep a lightweight block index sidecar so annotation anchoring
        # metadata is always rebuilt through the same helper Runtime uses after
        # accepted patches.
        write_block_index(context)
        AuditLog(context.root).append({"type": "annotation.created", "annotationId": annotation_id, "file": file_path, "blockId": block_id})
        if self.runtime is not None:
            self.runtime.refreshed_context()
        return {"annotation": annotation}

    def _next_annotation_id(self, context) -> str:  # noqa: ANN001 - internal ProjectContext helper
        max_seen = 0
        for annotation in context.annotations:
            if annotation.id.startswith("AN-") and annotation.id[3:].isdigit():
                max_seen = max(max_seen, int(annotation.id[3:]))
        return f"AN-{max_seen + 1:03d}"

    def chapter(self, file_path: str) -> Dict[str, Any]:
        if self.project_root is None:
            raise ProjectLoadError("No project is open. Create or open a project first.")
        rel_path = unquote(file_path)
        safe_chapter_path(self.project_root, rel_path)
        context = self._load_context()
        blocks = index_markdown_blocks(self.project_root, rel_path)
        if not blocks:
            raise ProjectLoadError(f"Unknown chapter: {rel_path}")
        ordered_blocks = dict(sorted((block_id, asdict(block)) for block_id, block in blocks.items()))
        chapter_title = markdown_title(self.project_root, rel_path)
        return {
            "file": rel_path,
            "title": chapter_title,
            "status": context.chapter_status.get(rel_path, "draft"),
            "editStatus": context.status_for_file(rel_path),
            "wordCount": manuscript_word_count("\n".join(block["text"] for block in ordered_blocks.values())),
            "blockIds": list(ordered_blocks),
            "blocks": ordered_blocks,
        }

    def run_skill(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        runtime = self._require_runtime()
        skill = _required_string(payload, "skill")
        annotation_ids = (
            payload.get("annotationIds")
            or payload.get("annotation_ids")
            or payload.get("annotations")
            or payload.get("annotation")
        )
        if isinstance(annotation_ids, str):
            annotation_ids = [annotation_ids]
        if annotation_ids is not None and not isinstance(annotation_ids, list):
            raise ValueError("annotationIds must be a string or array of strings.")
        with self._lock:
            return runtime.run_skill(
                skill,
                annotation_ids=annotation_ids,
                scope_file=_optional_string(payload, "file") or _optional_string(payload, "scopeFile"),
            )

    def ai_revise(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        runtime = self._require_runtime()
        annotation_ids = (
            payload.get("annotationIds")
            or payload.get("annotation_ids")
            or payload.get("annotations")
            or payload.get("annotation")
        )
        if isinstance(annotation_ids, str):
            annotation_ids = [annotation_ids]
        if not isinstance(annotation_ids, list) or not annotation_ids or not isinstance(annotation_ids[0], str):
            raise ValueError("annotationIds must include one annotation id for guarded AI revise.")
        annotation_id = annotation_ids[0]
        scope_file = _optional_string(payload, "file") or _optional_string(payload, "scopeFile")
        prefer_codex = not _bool_payload(payload, "forceRuntime")
        codex_timeout = _optional_number(payload, "timeoutSeconds") or DEFAULT_AI_REVISE_TIMEOUT_SECONDS
        fallback_reason = "codex_disabled"
        codex_summary: Dict[str, Any] | None = None

        if prefer_codex:
            try:
                context = runtime.refreshed_context()
                prompt = build_revise_with_annotations_prompt(context, annotation_id)
                codex_result = self.codex_client.run_patch_proposal_turn(
                    prompt=prompt,
                    cwd=self.project_root,
                    approval_handler=self._codex_approval_handler,
                    patch_validator=runtime.validate_patch,
                    timeout_seconds=codex_timeout,
                )
                codex_summary = summarize_codex_result(codex_result)
                proposal = codex_result.get("patchProposal")
                validation = codex_result.get("patchValidation")
                in_scope = proposal_matches_annotation_scope(context, annotation_id, proposal)
                if codex_result.get("ok") and validation_is_valid(validation) and patch_has_changes(proposal) and in_scope:
                    return {
                        "runId": codex_result.get("turnId") or codex_result.get("threadId") or "codex-run",
                        "skill": "revise-with-annotations",
                        "source": "codex-app-server",
                        "events": [{"type": "codex.patch.ready", "summary": codex_summary}],
                        "output": proposal,
                        "codex": codex_summary,
                    }
                if codex_result.get("error"):
                    fallback_reason = str(codex_result.get("error"))
                elif not validation_is_valid(validation):
                    fallback_reason = "codex_patch_failed_runtime_validation"
                elif not patch_has_changes(proposal):
                    fallback_reason = "codex_patch_had_no_changes"
                elif not in_scope:
                    fallback_reason = "codex_patch_out_of_annotation_scope"
                else:
                    fallback_reason = "codex_patch_not_usable"
            except Exception as exc:  # defensive UI boundary; fallback preserves local editing
                fallback_reason = str(exc)

        with self._lock:
            fallback = runtime.run_skill(
                "revise-with-annotations",
                annotation_ids=[annotation_id],
                scope_file=scope_file,
            )
        fallback["source"] = "runtime-deterministic"
        fallback["fallbackReason"] = fallback_reason
        if codex_summary is not None:
            fallback["codex"] = codex_summary
        return fallback

    def codex_skills(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        self._require_runtime()
        assert self.project_root is not None
        force_reload = not _bool_payload(payload, "noForceReload")
        return self.codex_client.list_skills(cwds=[self.project_root], force_reload=force_reload)

    def _codex_approval_handler(self, message: Dict[str, Any]) -> Dict[str, Any]:
        runtime = self._require_runtime()
        method = message.get("method")
        params = message.get("params") if isinstance(message.get("params"), dict) else message
        if method == "item/fileChange/requestApproval":
            return runtime.evaluate_file_change_request(params)
        if method == "item/commandExecution/requestApproval":
            return {"decision": "decline", "reason": "command_execution_requires_explicit_runtime_policy"}
        if method == "item/permissions/requestApproval":
            return {"decision": "decline", "reason": "permission_escalation_denied_by_default"}
        return {"decision": "decline", "reason": "unknown_appserver_request"}

    def codex_probe(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        self._require_runtime()
        assert self.project_root is not None
        prompt = _optional_string(payload, "prompt") or 'Return exactly JSON: {"ok": true, "source": "codex-app-server"}'
        timeout = _optional_number(payload, "timeoutSeconds")
        return self.codex_client.run_probe_turn(
            prompt=prompt,
            cwd=self.project_root,
            approval_handler=self._codex_approval_handler,
            timeout_seconds=timeout,
        )

    def codex_patch_probe(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        runtime = self._require_runtime()
        assert self.project_root is not None
        prompt = _optional_string(payload, "prompt") or (
            'Return exactly this JSON object and no markdown: '
            '{"id":"PP-probe","summary":"probe only","sourceAnnotations":["USER-codex-probe"],"rulesUsed":[],"changes":[]}'
        )
        timeout = _optional_number(payload, "timeoutSeconds")
        def validate_candidate(proposal: object) -> Dict[str, Any]:
            if isinstance(proposal, dict) and proposal.get("changes") == [] and proposal.get("id") == "PP-probe":
                return {"valid": True, "issues": [], "probeOnly": True}
            return runtime.validate_patch(proposal)

        return self.codex_client.run_patch_proposal_turn(
            prompt=prompt,
            cwd=self.project_root,
            approval_handler=self._codex_approval_handler,
            patch_validator=validate_candidate,
            timeout_seconds=timeout,
        )

    def preview_patch(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        runtime = self._require_runtime()
        patch = payload.get("patch", payload)
        with self._lock:
            return runtime.preview_patch(patch, allow_reviewed=_bool_payload(payload, "allowReviewed") or _bool_payload(payload, "allow_reviewed"))

    def apply_patch(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        runtime = self._require_runtime()
        patch = payload.get("patch", payload)
        with self._lock:
            return runtime.accept_patch(patch, allow_reviewed=_bool_payload(payload, "allowReviewed") or _bool_payload(payload, "allow_reviewed"))

    def create_project(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        result = create_book_project(
            self.workspace_root,
            title=_required_string(payload, "title"),
            slug=_optional_string(payload, "slug"),
            genre=_optional_string(payload, "genre") or "",
            premise=_optional_string(payload, "premise") or "",
            style=_optional_string(payload, "style") or "",
            chapter_title=_optional_string(payload, "chapterTitle") or "第一章",
            opening_text=_optional_string(payload, "openingText") or "",
        )
        AuditLog(result["root"]).append({"type": "project.created", "title": result["plan"]["title"]})
        result["summary"] = project_summary(result["root"], self.workspace_root)
        return result

    def manual_edit_patch(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        context = self._load_context()
        file_path = _required_string(payload, "file")
        block_id = _required_string(payload, "blockId")
        after_text = _required_string(payload, "afterText")
        block = context.block(file_path, block_id)
        patch = {
            "id": f"PP-manual-{block_id}",
            "summary": "Manual app edit proposal.",
            "sourceAnnotations": ["USER-manual-edit"],
            "changes": [
                {
                    "file": file_path,
                    "targetBlockId": block_id,
                    "operation": "replace_block",
                    "beforeHash": block.before_hash,
                    "afterText": after_text,
                    "reason": _optional_string(payload, "reason") or "manual app edit",
                }
            ],
        }
        validation = validate_patch(context, patch)
        patch["validation"] = {"valid": validation.valid, "issues": [issue.__dict__ for issue in validation.issues]}
        return patch

    def audit(self) -> Dict[str, Any]:
        if self.project_root is None:
            return {"events": []}
        return {"events": AuditLog(self.project_root).read()}


class BookWorkbenchHandler(BaseHTTPRequestHandler):
    server_version = "BookWorkbenchHTTP/0.1"

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        try:
            if not self._host_allowed():
                return
            if parsed.path == "/":
                self._send_html(self._index_html())
            elif parsed.path == "/api/health":
                self._send_json(self.app.health())
            elif parsed.path == "/api/workspace":
                self._send_json(self.app.workspace())
            elif parsed.path == "/api/projects":
                self._send_json(self.app.projects())
            elif parsed.path == "/api/project":
                self._send_json(self.app.project())
            elif parsed.path == "/api/annotations":
                self._send_json(self.app.annotations(parse_qs(parsed.query)))
            elif parsed.path == "/api/discussions":
                self._send_json(self.app.discussions())
            elif parsed.path == "/api/audit":
                self._send_json(self.app.audit())
            elif parsed.path == "/api/chapters":
                query = parse_qs(parsed.query)
                file_path = _first(query, "file")
                if not file_path:
                    project = self.app.project()
                    self._send_json({"chapters": sorted(project.get("blocks", []))})
                else:
                    self._send_json(self.app.chapter(file_path))
            elif parsed.path.startswith("/api/chapters/"):
                self._send_json(self.app.chapter(parsed.path[len("/api/chapters/") :]))
            else:
                self._send_error(HTTPStatus.NOT_FOUND, f"Unknown endpoint: {parsed.path}")
        except Exception as exc:
            self._send_exception(exc)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        try:
            if not self._host_allowed():
                return
            if not self._origin_allowed():
                return
            if not self._json_content_type_allowed():
                return
            if not self._token_allowed(parsed):
                return
            payload = self._read_json()
            if parsed.path == "/api/skills/run":
                self._send_json(self.app.run_skill(payload))
            elif parsed.path == "/api/ai/revise":
                self._send_json(self.app.ai_revise(payload))
            elif parsed.path == "/api/projects/open":
                self._send_json(self.app.open_project(payload))
            elif parsed.path in {"/api/patch/preview", "/api/patches/preview"}:
                self._send_json(self.app.preview_patch(payload))
            elif parsed.path in {"/api/patch/apply", "/api/patches/apply"}:
                self._send_json(self.app.apply_patch(payload))
            elif parsed.path == "/api/projects/create":
                self._send_json(self.app.create_project(payload))
            elif parsed.path == "/api/annotations/create":
                self._send_json(self.app.create_annotation(payload))
            elif parsed.path == "/api/discussions/create":
                self._send_json(self.app.create_discussion(payload))
            elif parsed.path == "/api/patch/manual":
                self._send_json(self.app.manual_edit_patch(payload))
            elif parsed.path == "/api/codex/health":
                self._send_json({"codex": self.app.codex_client.health()})
            elif parsed.path == "/api/codex/skills":
                self._send_json(self.app.codex_skills(payload))
            elif parsed.path == "/api/codex/probe":
                self._send_json(self.app.codex_probe(payload))
            elif parsed.path == "/api/codex/patch-probe":
                self._send_json(self.app.codex_patch_probe(payload))
            else:
                self._send_error(HTTPStatus.NOT_FOUND, f"Unknown endpoint: {parsed.path}")
        except Exception as exc:
            self._send_exception(exc)

    @property
    def app(self) -> RuntimeWebApp:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        if getattr(self.server, "quiet", False):  # type: ignore[attr-defined]
            return
        super().log_message(format, *args)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Request JSON body must be an object.")
        return payload

    def _index_html(self) -> str:
        token = getattr(self.server, "local_token", "")  # type: ignore[attr-defined]
        return INDEX_HTML.replace("__BOOKWORKBENCH_TOKEN__", json.dumps(token))

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: Any, *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message, "status": status.value}, status=status)

    def _send_exception(self, exc: Exception) -> None:
        if isinstance(exc, (ValueError, KeyError, json.JSONDecodeError, ProjectLoadError, ProjectCreationError, RuntimeErrorBase)):
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
        else:
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def _host_allowed(self) -> bool:
        raw_host = self.headers.get("Host")
        if not raw_host:
            self._send_error(HTTPStatus.FORBIDDEN, "Missing Host header.")
            return False
        try:
            parsed = urlparse(f"//{raw_host}")
        except ValueError:
            self._send_error(HTTPStatus.FORBIDDEN, "Invalid Host header.")
            return False
        hostname = parsed.hostname
        if not hostname:
            self._send_error(HTTPStatus.FORBIDDEN, "Invalid Host header.")
            return False
        port = parsed.port
        server_host, server_port = self.server.server_address[:2]
        allowed_hosts = {"127.0.0.1", "localhost", "::1", str(server_host)}
        if hostname not in allowed_hosts:
            self._send_error(HTTPStatus.FORBIDDEN, "Host header is not allowed.")
            return False
        if port is not None and int(port) != int(server_port):
            self._send_error(HTTPStatus.FORBIDDEN, "Host port is not allowed.")
            return False
        return True

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        expected = f"http://{self.headers.get('Host')}"
        if origin == expected:
            return True
        self._send_error(HTTPStatus.FORBIDDEN, "Cross-origin POST requests are not allowed.")
        return False

    def _json_content_type_allowed(self) -> bool:
        if self.headers.get_content_type() == "application/json":
            return True
        self._send_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "POST requests must use Content-Type: application/json.")
        return False

    def _token_allowed(self, parsed_path) -> bool:  # noqa: ANN001 - urlparse return type varies by Python version
        expected = getattr(self.server, "local_token", "")  # type: ignore[attr-defined]
        if not expected:
            return True
        provided = self.headers.get("X-BookWorkbench-Token", "")
        if not provided:
            auth_header = self.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                provided = auth_header[len("Bearer ") :]
        if secrets.compare_digest(provided, expected):
            return True
        self._send_error(HTTPStatus.FORBIDDEN, "Missing or invalid local API token.")
        return False


def create_server(
    project_root: str | Path | None = None,
    *,
    workspace_root: str | Path | None = None,
    builtin_skills_root: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    codex_client: CodexAppServerClient | None = None,
    local_token: str | None = None,
    quiet: bool = False,
) -> ThreadingHTTPServer:
    app = RuntimeWebApp(
        project_root,
        workspace_root=workspace_root,
        builtin_skills_root=builtin_skills_root,
        codex_client=codex_client,
    )
    server = ThreadingHTTPServer((host, port), BookWorkbenchHandler)
    server.app = app  # type: ignore[attr-defined]
    server.local_token = local_token if local_token is not None else secrets.token_urlsafe(24)  # type: ignore[attr-defined]
    server.quiet = quiet  # type: ignore[attr-defined]
    return server


def serve(
    project_root: str | Path | None = None,
    *,
    workspace_root: str | Path | None = None,
    builtin_skills_root: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
) -> None:
    server = create_server(
        project_root,
        workspace_root=workspace_root,
        builtin_skills_root=builtin_skills_root,
        host=host,
        port=port,
    )
    actual_host, actual_port = server.server_address[:2]
    url = f"http://{actual_host}:{actual_port}/"
    print(f"{APP_TITLE} running at {url}", flush=True)
    print(f"Local API token: {server.local_token}", flush=True)  # type: ignore[attr-defined]
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止书稿工作台本地服务")
    finally:
        server.server_close()


def _first(query: Mapping[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _powerbook_workflow_summary(root: Path) -> Dict[str, Any] | None:
    import_path = root / ".bookai" / "powerbook-import.json"
    if not import_path.exists():
        return None
    try:
        imported = json.loads(import_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    chapters = imported.get("chapters") if isinstance(imported, dict) else []
    status_counts: Dict[str, int] = {}
    if isinstance(chapters, list):
        for chapter in chapters:
            if not isinstance(chapter, dict):
                continue
            status = str(chapter.get("reviewStatus") or chapter.get("bookWorkbenchStatus") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
    artifacts: list[Dict[str, str]] = []
    artifact_specs = [
        ("审阅简报模板", "templates/review_brief.md"),
        ("作者决策模板", "templates/author_decisions.md"),
        ("修订日志模板", "templates/revision_log.md"),
        ("事实登记表", "claims/claim_register.yaml"),
        ("审阅收件箱", "reviews/inbox/README.md"),
    ]
    for label, relative in artifact_specs:
        path = root / relative
        if path.exists():
            artifacts.append({"label": label, "path": relative, "kind": "file"})
    resolved_logs = sorted((root / "reviews" / "resolved").glob("*.md")) if (root / "reviews" / "resolved").exists() else []
    if resolved_logs:
        artifacts.append({"label": f"已完成修订日志（{len(resolved_logs)} 个）", "path": "reviews/resolved", "kind": "directory"})
    return {
        "source": "PowerBook",
        "sourceTreeHash": imported.get("sourceTreeHash") if isinstance(imported, dict) else "",
        "statusCounts": status_counts,
        "statusMapping": imported.get("statusMapping", {}) if isinstance(imported, dict) else {},
        "artifacts": artifacts,
    }


def _bool_query(query: Mapping[str, list[str]], key: str) -> bool:
    value = _first(query, key)
    return value is not None and value.lower() not in {"0", "false", "no", "off", ""}


def _bool_payload(payload: Mapping[str, Any], key: str) -> bool:
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() not in {"0", "false", "no", "off", ""}
    return bool(value)


def _optional_number(payload: Mapping[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raise ValueError(f"{key} must be a number when provided.")


def _optional_string(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string.")
    return value


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = _optional_string(payload, key)
    if not value:
        raise ValueError(f"Missing required field: {key}")
    return value
