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
from .discussion_engine import append_discussion, list_discussions
from .patch_engine import validate_patch
from .project import ProjectLoadError, load_project, safe_chapter_path
from .project_creator import ProjectCreationError, create_book_project
from .workspace import list_projects, project_summary, resolve_workspace_project
from .runtime import RuntimeErrorBase, RuntimeOrchestrator


APP_TITLE = "BookWorkbench Local App"


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>BookWorkbench</title>
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
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
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
    .stats { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 16px; }
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
    .metric { padding: 18px; min-height: 108px; display: grid; grid-template-columns: 50px 1fr; gap: 12px; align-items: center; }
    .metric-icon, .small-icon { width: 46px; height: 46px; border-radius: 16px; display: grid; place-items: center; font-size: 23px; }
    .metric-icon.purple, .small-icon.purple { background: var(--purple-soft); color: var(--purple); }
    .metric-icon.green, .small-icon.green { background: var(--green-soft); color: var(--green); }
    .metric-icon.blue, .small-icon.blue { background: #eaf2ff; color: #3f7fe8; }
    .metric-icon.orange, .small-icon.orange { background: var(--orange-soft); color: var(--orange); }
    .metric .label { color: var(--muted); font-size: 14px; }
    .metric .num { display: block; margin: 4px 0; font-size: 28px; font-weight: 760; letter-spacing: -.03em; }
    .chapter-table { width: 100%; border-collapse: collapse; font-size: 14px; }
    .chapter-table th { color: #7b86a0; text-align: left; font-weight: 600; padding: 12px 10px; border-bottom: 1px solid var(--line); }
    .chapter-table td { padding: 12px 10px; border-bottom: 1px solid #edf1f6; }
    .chapter-table tr:hover { background: #fafbff; }
    .status-pill { display: inline-flex; align-items: center; gap: 5px; border-radius: 8px; padding: 4px 8px; font-size: 12px; font-weight: 650; }
    .status-draft { background: var(--orange-soft); color: var(--orange); }
    .status-reviewed { background: var(--green-soft); color: var(--green); }
    .status-locked { background: #eaf2ff; color: #3f7fe8; }
    .status-unreviewed { background: var(--purple-soft); color: var(--purple); }
    .side-stack { display: grid; gap: 16px; }
    .toolbar { min-height: 76px; padding: 14px 16px; display: flex; align-items: center; gap: 14px; border-bottom: 1px solid var(--line); }
    .select-like { border: 1px solid #dfe5f0; border-radius: 10px; padding: 10px 14px; background: #fff; min-width: 190px; font-weight: 700; }
    .toolbar .spacer, .spacer { flex: 1; }
    .editor-card { overflow: hidden; }
    .doc { padding: 38px 42px 24px; background: #fff; min-height: 560px; }
    .doc h2 { font-size: 25px; margin: 0 0 28px; }
    .paragraph { display: grid; grid-template-columns: 92px minmax(0, 1fr) 110px; gap: 16px; align-items: start; margin: 22px 0; line-height: 2; font-size: 17px; }
    .pid { color: #7d8aa4; font-size: 13px; padding-top: 8px; }
    .ptext { padding: 3px 6px; border-radius: 8px; white-space: pre-wrap; user-select: text; }
    .paragraph.annotated .ptext { background: linear-gradient(90deg, rgba(101,80,255,.16), rgba(101,80,255,.05)); }
    .tag { display: inline-flex; align-items: center; justify-content: center; min-width: 58px; padding: 3px 8px; border-radius: 7px; background: var(--purple-soft); color: var(--purple); font-size: 13px; font-weight: 750; }
    .editor-footer { display: flex; gap: 24px; align-items: center; border-top: 1px solid var(--line); padding: 14px 24px; color: #6b7892; font-size: 14px; }
    .annotation-card { border: 1px solid var(--line); border-radius: 14px; padding: 14px; margin-bottom: 12px; background: #fff; }
    .discussion-card { border: 1px solid #dfe5f0; border-radius: 14px; padding: 14px; margin-bottom: 12px; background: #fbfcff; }
    .discussion-card strong { color: var(--purple); }
    .discussion-card p { margin: 10px 0 0; line-height: 1.7; }
    .annotation-card.active { border-color: #d1c9ff; background: #fbfaff; box-shadow: 0 10px 24px rgba(91,61,245,.08); }
    .annotation-head { display: flex; justify-content: space-between; gap: 8px; color: var(--muted); font-size: 13px; }
    .annotation-card strong { color: var(--purple); }
    .annotation-card p { margin: 11px 0; line-height: 1.7; }
    .ai-item { display: grid; grid-template-columns: 42px 1fr auto; gap: 10px; align-items: center; padding: 10px 0; border-bottom: 1px solid #edf1f6; }
    .button-row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 14px; }
    .button-row.single { grid-template-columns: 1fr; }
    .diff-top { display: flex; align-items: center; gap: 16px; margin-bottom: 18px; }
    .back-btn { width: 46px; height: 46px; border-radius: 10px; border: 1px solid #dfe5f0; background: #fff; color: var(--text-soft); font-size: 24px; }
    .change-meta { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0; overflow: hidden; margin-bottom: 16px; }
    .meta-cell { padding: 14px 18px; border-right: 1px solid var(--line); display: flex; gap: 12px; align-items: center; }
    .meta-cell:last-child { border-right: 0; }
    .diff-columns { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .diff-col { padding: 18px; background: #fff; border: 1px solid var(--line); border-radius: 14px; min-height: 340px; }
    .diff-line { display: grid; grid-template-columns: 34px 1fr 18px; gap: 10px; padding: 10px; border-radius: 8px; margin: 7px 0; line-height: 1.7; }
    .diff-line.minus { background: var(--red-soft); }
    .diff-line.plus { background: var(--green-soft); }
    .diff-line .ln { color: #8d99ad; }
    .diff-reason { margin-top: 16px; padding: 18px 20px; }
    .diff-raw { margin-top: 12px; background: #0f172a; color: #e2e8f0; border-radius: 12px; padding: 14px; max-height: 190px; overflow: auto; font-size: 12px; }
    .check-list { border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
    .check-row { display: grid; grid-template-columns: 28px 1fr auto; gap: 10px; padding: 12px; border-bottom: 1px solid var(--line); align-items: center; }
    .check-row:last-child { border-bottom: 0; }
    .commit-box { border: 1px solid var(--line); border-radius: 10px; padding: 12px; background: #fbfcff; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; overflow: auto; }
    .rules-main { display: grid; grid-template-columns: 42% minmax(0, 1fr); gap: 18px; align-items: start; }
    .tabs { display: flex; gap: 22px; border-bottom: 1px solid var(--line); padding: 0 16px; }
    .tab { border: 0; background: transparent; color: #64728a; padding: 14px 0; font-weight: 650; }
    .tab.active { color: var(--purple); box-shadow: inset 0 -2px 0 var(--purple); }
    .rule-list { padding: 10px 12px 14px; }
    .rule-row { border-bottom: 1px solid #edf1f6; border-radius: 12px; padding: 14px; display: grid; gap: 8px; }
    .rule-meta { display: flex; justify-content: space-between; color: var(--muted); font-size: 13px; }
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
      .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .search { width: 300px; }
    }
    @media (max-width: 760px) {
      .app { display: block; }
      .sidebar { position: static; height: auto; }
      .nav { grid-template-columns: repeat(3, 1fr); }
      .topbar { flex-wrap: wrap; }
      .search { order: 3; width: 100%; }
      .stats, .diff-columns, .change-meta, .form-grid { grid-template-columns: 1fr; }
      .paragraph { grid-template-columns: 1fr; gap: 4px; }
    }
  </style>
</head>
<body data-app-title="BookWorkbench Local App">
  <div class="app">
    <aside class="sidebar">
      <div class="brand"><div class="logo">🪶</div><div><strong>Manuscript Workbench</strong><span>文稿工作台</span></div></div>
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
      <div class="storage"><div class="storage-row"><span>当前 workspace</span><span id="workspaceShort">本地</span></div><div class="bar"><i id="workspaceBar"></i></div></div>
      <div class="help-card"><span>ⓘ 安全模式：Patch 审核</span><span>✓</span></div>
    </aside>

    <main class="main">
      <header class="topbar">
        <div class="title-wrap"><h1 id="pageTitle">项目</h1><div id="pageSubtitle" class="subtitle">本地工作台已启动。先选择或新建一个书稿项目。</div></div>
        <label class="search">⌕ <input placeholder="搜索章节、文稿、批注..." aria-label="搜索"><span class="kbd">⌘K</span></label>
        <div class="avatar"><div class="avatar-img"></div><span>本地作者</span></div>
      </header>

      <section id="view-dashboard" class="view grid dashboard">
        <div class="grid" id="dashboardMain"></div>
        <aside class="side-stack" id="dashboardSide"></aside>
      </section>

      <section id="view-editor" class="view grid editor hidden">
        <div class="card editor-card">
          <div class="toolbar"><div class="select-like" id="chapterSelect">未选择章节</div><span class="status-pill status-draft" id="editorStatus">草稿</span><span class="muted" id="editorWords">字数：0</span><div class="spacer"></div><button class="ghost-btn" id="saveBtn">✓ 保存</button><button class="ghost-btn" id="manualPatchBtn">生成手动 Patch</button><button class="primary-btn" id="generateSuggestionBtn">✦ AI 处理批注</button></div>
          <article class="doc" id="docView"></article>
          <footer class="editor-footer"><span id="chapterWordCount">本章字数：0</span><span>所有正文修改必须先进入 Diff</span><span>Runtime 安全边界 <span class="ok">●</span></span><span class="spacer"></span><span id="selectionInfo">可选中文本后添加批注</span></footer>
        </div>
        <aside class="side-stack">
          <section class="card panel"><div class="panel-title"><h3>批注与 AI 建议</h3><button class="ghost-btn" id="addAnnotationGlobalBtn">添加批注</button></div><div class="tabs"><button class="tab active">批注 (<span id="annotationCount">0</span>)</button><button class="tab">AI 建议</button></div><div id="annotationPanel" class="mt"></div></section>
          <section class="card panel"><div class="panel-title"><h3>AI 解读与建议 ⓘ</h3><span class="muted">先生成 PatchProposal</span></div><div id="aiActionList"></div><div class="button-row"><button class="primary-btn" id="reviseCurrentBtn">处理当前批注</button><button class="ghost-btn" id="addRuleBtn">加入规则库</button></div><div class="button-row single"><button class="ghost-btn" id="laterBtn">稍后处理</button></div></section>
          <section class="card panel"><div class="stats" style="grid-template-columns:repeat(3,1fr); gap:8px"><div><strong id="ruleMatchRate">—</strong><br><span class="muted">规则匹配</span></div><div><strong class="bad" id="pendingAnnotationCount">0</strong><br><span class="muted">待处理批注</span></div><div><strong class="ok">✓</strong><br><span class="muted">Patch only</span></div></div></section>
        </aside>
      </section>

      <section id="view-diff" class="view grid diff hidden">
        <div>
          <div class="diff-top"><button class="back-btn" id="backToEditorBtn">‹</button><h1 style="font-size:24px" id="diffTitle">Patch / Diff 审核</h1><span class="tag" id="diffBlockTag">—</span><div class="spacer"></div><button class="ghost-btn" id="refreshDiffBtn">重新预览</button></div>
          <div class="card change-meta"><div class="meta-cell"><span class="small-icon purple">☵</span><div><span class="muted">来源批注</span><br><strong id="sourceAnnotation">—</strong></div></div><div class="meta-cell"><span class="small-icon purple">✦</span><div><span class="muted">应用规则</span><br><strong id="rulesUsed">—</strong></div></div><div class="meta-cell"><span class="small-icon blue">▣</span><div><span class="muted">写入方式</span><br><strong>Runtime Patch</strong></div></div><div class="meta-cell"><span class="small-icon green">✓</span><div><span class="muted">状态</span><br><strong id="patchValidity">待预览</strong></div></div></div>
          <div class="diff-columns"><div class="diff-col"><h3>原文（修改前） <span class="tag" id="beforeBlockTag">—</span></h3><div id="beforeLines"></div></div><div class="diff-col"><h3>建议（修改后） <span class="tag" id="afterBlockTag">—</span></h3><div id="afterLines"></div></div></div>
          <section class="card diff-reason"><div class="panel-title"><h3>ⓘ 修改原因 / Runtime 校验</h3><button class="link-btn">⌃</button></div><p id="changeReason" class="muted">所有 AI 输出只能在这里预览，接受后才写入。</p><pre class="diff-raw" id="rawDiff"></pre></section>
        </div>
        <aside class="side-stack"><div class="button-row" style="grid-template-columns:1fr 1fr 1fr"><button class="danger-btn" id="rejectPatchBtn">拒绝</button><button class="ghost-btn" id="partialPatchBtn" disabled title="暂不可用：当前只支持接受完整安全 Patch">部分应用（暂不可用）</button><button class="primary-btn" id="acceptPatchBtn">接受并提交</button></div><section class="card panel"><div class="panel-title"><h3>本次变更</h3></div><div class="check-list" id="changeCheckList"></div><h3 class="mt">Git 提交预览</h3><div class="commit-box" id="commitPreview">等待 PatchProposal</div></section><section class="card panel"><div class="panel-title"><h3>影响范围</h3></div><div class="check-list"><div class="check-row"><span>▤</span><span>当前段落</span><strong id="impactBlock">—</strong></div><div class="check-row"><span>🔒</span><span>锁定章节</span><strong class="ok">自动拒绝</strong></div><div class="check-row"><span>⊕</span><span>其他章节</span><strong>不修改</strong></div></div></section></aside>
      </section>

      <section id="view-rules" class="view rules-layout hidden"><div class="rules-main"><section class="card"><div class="toolbar"><button class="ghost-btn" id="newRuleBtn" disabled title="暂不可用：规则目前由批注提炼生成">＋ 新建规则（暂不可用）</button><button class="ghost-btn">≋ 筛选</button><button class="ghost-btn" id="batchApplyBtn" disabled title="暂不可用：批量应用需要先生成 PatchProposal">▣ 批量应用（暂不可用）</button></div><div class="tabs"><button class="tab active">全部（<span id="ruleCount">0</span>）</button><button class="tab">风格</button><button class="tab">结构</button><button class="tab">设定</button></div><div class="rule-list" id="ruleList"></div></section><div class="grid"><section class="card panel" id="ruleDetail"></section><section class="card panel"><h3>规则传播安全边界</h3><div class="check-list"><div class="check-row"><span>✓</span><span>只影响 draft / unreviewed</span><strong class="ok">开启</strong></div><div class="check-row"><span>🔒</span><span>reviewed 需二次确认</span><strong class="ok">开启</strong></div><div class="check-row"><span>🔒</span><span>locked 永不修改</span><strong class="ok">开启</strong></div></div><div class="button-row single"><button class="ghost-btn" id="previewRuleImpactBtn" disabled title="暂不可用：规则影响预览尚未接入运行时">预览影响（暂不可用）</button></div></section></div></div></section>
      <section id="view-annotations" class="view hidden"><section class="card panel"><div class="panel-title"><h2>批注中心</h2><button class="primary-btn" id="annotationToEditorBtn">打开文稿处理</button></div><div id="annotationCenter"></div></section></section>
      <section id="view-discussions" class="view grid dashboard hidden"><section class="card panel"><div class="panel-title"><h2>项目讨论</h2><span class="muted">写作想法会进入 sidecar，不改正文</span></div><form id="discussionForm" class="grid"><label class="field">讨论内容<textarea id="discussionTextInput" required placeholder="例如：这一章想讨论人物动机、节奏或设定。"></textarea></label><div class="button-row single"><button class="primary-btn" id="submitDiscussionBtn" type="submit">保存讨论</button></div></form></section><aside class="side-stack"><section class="card panel"><div class="panel-title"><h3>讨论记录</h3><span class="tag" id="discussionCount">0</span></div><div id="discussionList"></div></section><section class="card panel"><h3>安全说明</h3><p class="muted">讨论只作为上下文记录，不会直接修改 chapters/*.md；改稿仍必须通过 PatchProposal 和 Diff 审核。</p></section></aside></section>
      <section id="view-export" class="view hidden"><section class="card panel"><h2>导出项目</h2><p class="muted">当前 MVP 保持 Markdown 原生项目结构，可直接复制项目目录。导出适配器后续会接 DOCX/PDF roundtrip。</p><button class="ghost-btn" id="copyProjectPathBtn">复制项目路径</button><pre class="diff-raw" id="projectPathBox"></pre></section></section>
      <section id="view-settings" class="view hidden"><section class="card panel"><h2>设置 / 安全中心</h2><div class="check-list"><div class="check-row"><span>🛡</span><span>AI 只生成建议，不自动写入</span><strong class="ok">开启</strong></div><div class="check-row"><span>🔒</span><span>锁定章节禁止修改</span><strong class="ok">开启</strong></div><div class="check-row"><span>▣</span><span>修改必须先生成 PatchProposal</span><strong class="ok">开启</strong></div><div class="check-row"><span>⌘</span><span>Codex app-server</span><strong id="settingsCodex">未检测</strong></div><div class="check-row"><span>□</span><span>Workspace</span><strong id="settingsWorkspace">—</strong></div></div></section></section>
    </main>
  </div>

  <div class="modal-backdrop hidden" id="projectModal" data-testid="new-project-modal">
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="projectModalTitle">
      <header><h2 id="projectModalTitle">新建书稿项目</h2><button class="ghost-btn" type="button" id="closeProjectModalBtn">关闭</button></header>
      <form id="projectForm">
        <div class="form-grid">
          <label class="field">书名<input id="projectTitleInput" name="title" required placeholder="例如：我的第一本书"></label>
          <label class="field">目录名（可选）<input id="projectSlugInput" name="slug" placeholder="my-first-book"></label>
        </div>
        <div class="form-grid">
          <label class="field">类型<input id="projectGenreInput" name="genre" placeholder="长篇小说 / 非虚构 / 剧本"></label>
          <label class="field">第一章标题<input id="projectChapterTitleInput" name="chapterTitle" placeholder="第一章"></label>
        </div>
        <label class="field">核心命题 / 简介<textarea id="projectPremiseInput" name="premise" placeholder="这本书想写什么？可以先留空。"></textarea></label>
        <label class="field">风格偏好<textarea id="projectStyleInput" name="style" placeholder="例如：克制、具体、少解释心理。可以先留空。"></textarea></label>
        <label class="field">开篇正文（可选；留空不会预置小说）<textarea id="projectOpeningInput" name="openingText" placeholder="如果你还没开始写，就保持为空。"></textarea></label>
      </form>
      <div class="modal-actions"><button class="ghost-btn" type="button" id="cancelProjectBtn">取消</button><button class="primary-btn" type="submit" form="projectForm" id="submitProjectBtn">创建项目</button></div>
    </div>
  </div>

  <div class="modal-backdrop hidden" id="annotationModal" data-testid="annotation-modal">
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="annotationModalTitle">
      <header><h2 id="annotationModalTitle">添加批注</h2><button class="ghost-btn" type="button" id="closeAnnotationModalBtn">关闭</button></header>
      <form id="annotationForm">
        <input type="hidden" id="annotationFileInput">
        <input type="hidden" id="annotationBlockInput">
        <label class="field">选中文本<textarea id="annotationSelectedInput" required></textarea></label>
        <label class="field">批注内容<textarea id="annotationBodyInput" required placeholder="写下你的修改意见"></textarea></label>
      </form>
      <div class="modal-actions"><button class="ghost-btn" type="button" id="cancelAnnotationBtn">取消</button><button class="primary-btn" type="submit" form="annotationForm" id="submitAnnotationBtn">保存批注</button></div>
    </div>
  </div>

  <div class="toast" id="toast" role="status" aria-live="polite"></div>

  <script>
    const BOOKWORKBENCH_TOKEN = __BOOKWORKBENCH_TOKEN__;
    const state = { workspace: null, projects: [], project: null, annotations: [], discussions: [], audit: [], currentFile: null, currentChapter: null, lastPatch: null, lastPreview: null, activeView: "dashboard", selectedAnnotationId: null };
    const $ = (id) => document.getElementById(id);
    function escapeHtml(value) { const span = document.createElement("span"); span.textContent = String(value ?? ""); return span.innerHTML; }
    function hasProject() { return !!(state.project && state.project.open !== false && state.project.root); }
    function toast(message) { const el = $("toast"); el.textContent = message; el.classList.add("show"); clearTimeout(toast.timer); toast.timer = setTimeout(() => el.classList.remove("show"), 2800); }
    async function api(path, options = {}) { const headers = { "Content-Type": "application/json", "X-BookWorkbench-Token": BOOKWORKBENCH_TOKEN, ...(options.headers || {}) }; const response = await fetch(path, { ...options, headers }); const text = await response.text(); let payload; try { payload = text ? JSON.parse(text) : {}; } catch (_) { payload = { raw: text }; } if (!response.ok) throw new Error(payload.error || response.statusText); return payload; }
    async function maybeApi(path) { try { return await api(path); } catch (_) { return null; } }
    function bookTitle() { const spec = state.project?.bookSpec || ""; const match = spec.match(/#\s*(.+?)\s*Book SPEC/); return match ? match[1].trim() : (state.project?.title || "未命名作品"); }
    function wordCount(text) { return String(text || "").replace(/<!--.*?-->/gs, "").replace(/\s+/g, "").length; }
    function statusLabel(status) { return ({ draft: "草稿", reviewed: "已审阅", locked: "已锁定", unreviewed: "未审阅" })[status] || status || "草稿"; }
    function statusClass(status) { return `status-${status || "draft"}`; }
    function titleFromPath(path) { return String(path || "").split("/").pop().replace(/\.md$/, ""); }
    function splitLines(text) { const lines = String(text || "").split(/\\n+/).filter(Boolean); return lines.length ? lines : ["（空）"]; }
    function firstChapterFile() { const files = Object.keys(state.project?.blocks || {}).sort(); return files[0] || null; }
    function blockAnnotation(blockId) { return state.annotations.find((item) => item.block_id === blockId || item.blockId === blockId); }
    function selectedAnnotation() { if (state.selectedAnnotationId) return state.annotations.find((item) => item.id === state.selectedAnnotationId) || null; return state.annotations.find((item) => item.status === "open") || state.annotations[0] || null; }
    function showError(error) { console.error(error); toast(String(error.message || error)); $("pageSubtitle").innerHTML = `<span class="bad">发生错误：</span>${escapeHtml(error.message || error)}`; }
    function setProjectNavEnabled(enabled) { document.querySelectorAll("[data-project-only]").forEach((button) => { button.disabled = !enabled; }); }
    function setView(view) { if (view !== "dashboard" && view !== "settings" && !hasProject()) { toast("请先新建或打开一个项目。"); view = "dashboard"; } state.activeView = view; document.querySelectorAll(".view").forEach((el) => el.classList.add("hidden")); const target = $(`view-${view}`); if (target) target.classList.remove("hidden"); document.querySelectorAll(".nav button").forEach((btn) => btn.classList.toggle("active", btn.dataset.view === view)); const titles = { dashboard: hasProject() ? [bookTitle(), "项目总览 · 点击章节进入文稿"] : ["项目", "还没有打开书稿。请从项目列表进入，或新建一个项目。"], editor: ["文稿编辑", `项目 / ${bookTitle()} / 文稿`], annotations: ["批注中心", "集中处理开放批注与 AI 建议"], discussions: ["项目讨论", "讨论写作意图，不直接改正文"], diff: ["Patch / Diff 审核", "所有修改先预览，再由 Runtime 安全应用"], rules: ["规则中心", "规则只传播到 draft / unreviewed，locked 永不修改"], export: ["导出项目", "Markdown 项目结构可直接复制或进入导出适配器"], settings: ["设置 / 安全中心", "安全边界、Codex 连接与审计策略"] }; const [title, subtitle] = titles[view] || titles.dashboard; $("pageTitle").textContent = title; $("pageSubtitle").textContent = subtitle; if (view === "dashboard") renderDashboard(); if (view === "rules") renderRules(); if (view === "annotations") renderAnnotations(); if (view === "discussions") renderDiscussions(); if (view === "diff") renderDiffIfReady(); }
    function codexStatusLabel(codex) { if (codex?.ok) return "已连接"; if (codex?.error) return `连接失败：${codex.error}`; const labels = { pending_project_open: "打开项目后检测", no_project_open: "打开项目后检测", unavailable: "未连接", timeout: "连接超时" }; return labels[codex?.status] || "未连接"; }
    async function loadHealth() { const health = await api("/api/health"); const codexOk = !!health.codex?.ok; const runtimeOk = !!health.runtime?.ok; const codexLabel = codexStatusLabel(health.codex); $("settingsCodex").textContent = codexLabel; $("settingsCodex").title = codexLabel; $("settingsCodex").className = codexOk ? "ok" : "bad"; if (hasProject()) $("pageSubtitle").innerHTML = `Runtime <span class="${runtimeOk ? "ok" : "bad"}">${runtimeOk ? "已连接" : "未打开项目"}</span> · Codex <span class="${codexOk ? "ok" : "bad"}">${escapeHtml(codexLabel)}</span>`; return health; }
    async function loadWorkspace() { const [workspace, projects, project] = await Promise.all([api("/api/workspace"), api("/api/projects"), maybeApi("/api/project")]); state.workspace = workspace; state.projects = projects.projects || []; if (project && project.open !== false) state.project = project; $("settingsWorkspace").textContent = workspace.root || "—"; $("workspaceShort").textContent = workspace.root ? workspace.root.split("/").slice(-1)[0] : "本地"; $("workspaceBar").style.width = `${Math.min(100, state.projects.length * 12)}%`; renderShell(); if (hasProject()) await loadProjectDetails(false); else renderDashboard(); }
    async function refreshProjects() { const payload = await api("/api/projects"); state.projects = payload.projects || []; renderDashboard(); return state.projects; }
    async function openProject(relativePath, options = {}) { const payload = await api("/api/projects/open", { method: "POST", body: JSON.stringify({ relativePath }) }); state.project = payload.project; state.currentFile = null; state.currentChapter = null; state.lastPatch = null; state.lastPreview = null; await loadProjectDetails(true); setView("dashboard"); await loadHealth().catch(() => {}); if (!options.quiet) toast(`已打开：${bookTitle()}`); return state.project; }
    async function loadProjectDetails(loadFirst = true) { if (!hasProject()) return null; const [project, annotationPayload, discussionPayload, auditPayload] = await Promise.all([api("/api/project"), api("/api/annotations?include_resolved=1"), api("/api/discussions"), api("/api/audit")]); state.project = project; state.annotations = annotationPayload.annotations || []; state.discussions = discussionPayload.discussions || []; state.audit = auditPayload.events || []; state.selectedAnnotationId = state.annotations[0]?.id || null; state.currentFile = state.currentFile || firstChapterFile(); renderShell(); renderDashboard(); renderRules(); renderAnnotations(); if (loadFirst && state.currentFile) await loadChapter(state.currentFile); await loadHealth().catch(() => {}); return project; }
    async function loadChapter(file) { if (!file) return null; state.currentFile = file; state.currentChapter = await api("/api/chapters/" + encodeURIComponent(file)); renderEditor(); return state.currentChapter; }
    function renderShell() { setProjectNavEnabled(hasProject()); $("sideBookTitle").textContent = hasProject() ? bookTitle() : "未打开项目"; $("sideBookMeta").textContent = hasProject() ? `${Object.keys(state.project.blocks || {}).length} 章 · ${state.annotations.length} 条批注 · ${state.discussions.length} 条讨论` : "请选择或新建书稿项目"; }
    function renderDashboard() { const main = $("dashboardMain"); const side = $("dashboardSide"); if (!hasProject()) { const hasProjects = state.projects.length > 0; main.innerHTML = hasProjects ? `<section class="card panel" data-testid="project-list-panel"><div class="panel-title"><div><h2>项目列表</h2><p class="muted">请选择一个已有书稿项目，或新建自己的项目。</p></div><div class="button-row"><button class="ghost-btn" id="refreshProjectListBtn">刷新</button><button class="primary-btn" id="workspaceNewProjectBtn" data-testid="open-new-project-modal">新建项目</button></div></div><div class="project-grid">${state.projects.map(projectCardHtml).join("")}</div></section>` : `<section class="card empty-state" data-testid="empty-workspace"><div><div class="empty-icon">＋</div><h2>还没有书稿项目</h2><p>BookWorkbench 不会预置任何小说。请新建你自己的项目，或打开 workspace 中已有的 Markdown 书稿项目。</p><button class="primary-btn" id="emptyNewProjectBtn" data-testid="open-new-project-modal">新建项目</button></div></section>`; side.innerHTML = `<section class="card panel"><div class="panel-title"><h3>安全底座</h3></div><div class="check-list"><div class="check-row"><span>▣</span><span>AI 输出必须是 PatchProposal</span><strong class="ok">开启</strong></div><div class="check-row"><span>🔒</span><span>locked 章节禁止修改</span><strong class="ok">开启</strong></div><div class="check-row"><span>⌘</span><span>接受 Patch 后 Git checkpoint</span><strong class="ok">开启</strong></div></div></section>`; bindProjectListEvents(); $("emptyNewProjectBtn")?.addEventListener("click", openProjectModal); $("workspaceNewProjectBtn")?.addEventListener("click", openProjectModal); return; }
      const files = Object.keys(state.project.blocks || {}).sort(); const statuses = state.project.chapterStatus || {}; const open = state.annotations.filter((a) => a.status === "open").length; const reviewed = Object.values(statuses).filter((s) => s === "reviewed").length; const locked = Object.values(statuses).filter((s) => s === "locked").length; const rules = state.project.rules || []; const stats = [["📖", "章节", files.length, "真实文件", "purple"], ["✓", "已审阅", reviewed, "需二次确认", "green"], ["🔒", "已锁定", locked, "自动拒绝", "blue"], ["☵", "待处理批注", open, "sidecar", "orange"], ["✦", "活跃规则", rules.length, "Patch only", "purple"], ["✎", "讨论", state.discussions.length, "sidecar", "blue"]]; main.innerHTML = `<div class="stats">${stats.map(([icon, label, num, note, color]) => `<div class="card metric"><div class="metric-icon ${color}">${icon}</div><div><span class="label">${label}</span><strong class="num">${num}</strong><span class="note muted">${note}</span></div></div>`).join("")}</div><section class="card panel"><div class="panel-title"><h2>章节列表</h2><button class="ghost-btn" id="refreshProjectBtn">刷新</button></div><table class="chapter-table"><thead><tr><th>章节</th><th>状态</th><th>块数</th><th>字数</th><th></th></tr></thead><tbody id="chapterRows">${files.map((file, idx) => { const blocks = state.project.blocks[file] || []; return `<tr data-file="${escapeHtml(file)}"><td><strong>${idx + 1}. ${escapeHtml(titleFromPath(file))}</strong><br><span class="muted">${escapeHtml(file)}</span></td><td><span class="status-pill ${statusClass(statuses[file])}">${statusLabel(statuses[file])}</span></td><td>${blocks.length}</td><td id="words-${idx}">—</td><td>打开 ›</td></tr>`; }).join("")}</tbody></table></section>`; side.innerHTML = `<section class="card panel"><div class="panel-title"><h3>快捷操作</h3></div><div class="button-row single"><button class="primary-btn" id="newProjectBtn">⊕ 新建项目</button><button class="ghost-btn" id="openEditorBtn">▤ 打开文稿</button><button class="ghost-btn" id="newAnnotationBtn">☵ 处理批注</button><button class="ghost-btn" id="newDiscussionBtn">✎ 新建讨论</button></div></section><section class="card panel"><div class="panel-title"><h3>项目列表</h3><button class="link-btn" id="refreshProjectListBtn">刷新</button></div><div class="project-grid" style="grid-template-columns:1fr">${state.projects.map(projectCardHtml).join("")}</div></section><section class="card panel"><div class="panel-title"><h3>AI 建议</h3></div><div id="aiSuggestions">${open ? `有 ${open} 条批注可处理。` : "当前没有待处理批注。"}</div></section><section class="card panel"><div class="panel-title"><h3>项目健康度</h3></div><div class="check-list"><div class="check-row"><span>▣</span><span>Runtime</span><strong class="ok">OK</strong></div><div class="check-row"><span>⌘</span><span>Git checkpoint</span><strong class="ok">接受 Patch 时创建</strong></div></div></section>`; document.querySelectorAll("#chapterRows tr").forEach((tr) => tr.addEventListener("click", () => loadChapter(tr.dataset.file).then(() => setView("editor")).catch(showError))); $("refreshProjectBtn")?.addEventListener("click", () => loadProjectDetails(false).catch(showError)); $("newProjectBtn")?.addEventListener("click", openProjectModal); $("openEditorBtn")?.addEventListener("click", () => { const first = firstChapterFile(); if (first) loadChapter(first).then(() => setView("editor")).catch(showError); }); $("newAnnotationBtn")?.addEventListener("click", () => setView("annotations")); $("newDiscussionBtn")?.addEventListener("click", () => setView("discussions")); bindProjectListEvents(); }
    function projectCardHtml(project) { return `<button class="project-card" data-project="${escapeHtml(project.relativePath)}" data-testid="project-card"><span class="tag">${escapeHtml(project.slug || project.relativePath)}</span><strong>${escapeHtml(project.title || project.relativePath)}</strong><div class="meta">${project.chapterCount || 0} 章 · ${project.annotationCount || 0} 条批注<br>${escapeHtml(project.relativePath)}</div></button>`; }
    function bindProjectListEvents() { document.querySelectorAll("[data-project]").forEach((card) => card.addEventListener("click", () => openProject(card.dataset.project).catch(showError))); $("refreshProjectListBtn")?.addEventListener("click", () => refreshProjects().catch(showError)); }
    function renderEditor() { const chapter = state.currentChapter; if (!chapter) return; const blocks = chapter.blocks || {}; const ids = chapter.blockIds || Object.keys(blocks); const title = chapter.title || titleFromPath(chapter.file); const allText = Object.values(blocks).map((b) => b.text).join("\\n"); const words = wordCount(allText); $("chapterSelect").textContent = title; $("editorStatus").textContent = statusLabel(chapter.status); $("editorStatus").className = `status-pill ${statusClass(chapter.status)}`; $("editorWords").textContent = `字数：${words.toLocaleString()}`; $("chapterWordCount").textContent = `本章字数：${words.toLocaleString()}`; $("docView").innerHTML = `<h2>${escapeHtml(title)}</h2>` + ids.map((id) => { const block = blocks[id]; const annotation = blockAnnotation(id); return `<div class="paragraph ${annotation ? "annotated" : ""}" data-block="${escapeHtml(id)}"><div class="pid">${escapeHtml(id)}</div><div class="ptext" data-testid="block-text">${escapeHtml(block.text)}</div><div><button class="ghost-btn add-annotation-btn" data-block="${escapeHtml(id)}">添加批注</button>${annotation ? `<br><span class="tag">${escapeHtml(annotation.id)}</span>` : ""}</div></div>`; }).join(""); document.querySelectorAll(".add-annotation-btn").forEach((btn) => btn.addEventListener("click", () => openAnnotationModal(btn.dataset.block))); renderAnnotationPanel(); renderDiffIfReady(); }
    function renderAnnotationPanel() { const list = state.annotations.filter((item) => !state.currentFile || item.file === state.currentFile); $("annotationCount").textContent = list.length; $("pendingAnnotationCount").textContent = list.filter((item) => item.status === "open").length; $("annotationPanel").innerHTML = list.length ? list.map((a) => `<button class="annotation-card ${a.id === state.selectedAnnotationId ? "active" : ""}" data-annotation="${escapeHtml(a.id)}"><div class="annotation-head"><strong>${escapeHtml(a.id)}</strong><span>${escapeHtml(a.file)} · ${escapeHtml(a.block_id)}</span></div><p>${escapeHtml(a.text)}</p><div class="annotation-head"><span>${escapeHtml(a.status)}</span><span>选择处理 ›</span></div></button>`).join("") : `<div class="empty-state" style="min-height:180px"><div><div class="empty-icon">☵</div><h2>当前章节暂无批注</h2><p>选中文稿中的句子，点击“添加批注”。批注会写入 .bookai/annotations.jsonl，不污染正文。</p></div></div>`; document.querySelectorAll("[data-annotation]").forEach((el) => el.addEventListener("click", () => { state.selectedAnnotationId = el.dataset.annotation; renderAnnotationPanel(); })); $("aiActionList").innerHTML = `<div class="ai-item"><span class="small-icon purple">✦</span><div><strong>局部改写</strong><br><span class="muted">只生成 PatchProposal，用户接受前不写正文</span></div><span class="muted">安全</span></div><div class="ai-item"><span class="small-icon blue">▣</span><div><strong>锚点校验</strong><br><span class="muted">beforeHash 不匹配时拒绝自动应用</span></div><span class="muted">强制</span></div>`; }
    function renderAnnotations() { $("annotationCenter").innerHTML = state.annotations.length ? state.annotations.map((a) => `<div class="annotation-card"><div class="annotation-head"><strong>${escapeHtml(a.id)}</strong><span>${escapeHtml(a.file)} · ${escapeHtml(a.block_id)}</span></div><p>${escapeHtml(a.text)}</p><span class="status-pill status-${a.status === "open" ? "draft" : "reviewed"}">${escapeHtml(a.status)}</span></div>`).join("") : `<div class="empty-state" style="min-height:260px"><div><div class="empty-icon">☵</div><h2>暂无批注</h2><p>批注会保存在 sidecar 文件中，不会插入正文。</p></div></div>`; }
    function renderDiscussions() { const list = state.discussions || []; $("discussionCount").textContent = list.length; $("discussionList").innerHTML = list.length ? list.map((item) => `<div class="discussion-card" data-testid="discussion-card"><div class="annotation-head"><strong>${escapeHtml(item.id)}</strong><span>${escapeHtml(item.file || "项目")}${item.blockId ? " · " + escapeHtml(item.blockId) : ""}</span></div><p>${escapeHtml(item.text)}</p></div>`).join("") : `<div class="empty-state" style="min-height:220px"><div><div class="empty-icon">✎</div><h2>暂无讨论</h2><p>先记录写作意图；后续改稿仍要走 Patch 审核。</p></div></div>`; }
    function renderRules() { if (!hasProject()) return; const rules = state.project?.rules || []; $("ruleCount").textContent = rules.length; $("ruleList").innerHTML = rules.length ? rules.map((rule, idx) => `<div class="rule-row ${idx === 0 ? "active" : ""}"><div class="rule-meta"><span>${escapeHtml(rule.id)}</span><span class="ok">启用 ●</span></div><strong>${escapeHtml(rule.text)}</strong><div class="rule-meta"><span>来源：${escapeHtml((rule.source_annotations || []).join("、") || "用户规则")}</span><span class="tag">${escapeHtml((rule.apply_to || []).join(" / ") || "全部")}</span></div></div>`).join("") : `<div class="empty-state" style="min-height:220px"><div><div class="empty-icon">▱</div><h2>暂无规则</h2><p>可以从批注中沉淀规则。</p></div></div>`; const rule = rules[0]; $("ruleDetail").innerHTML = rule ? `<div><strong>${escapeHtml(rule.id)}</strong> <span class="status-pill status-reviewed">启用</span></div><h2>${escapeHtml(rule.text)}</h2><p class="muted">作用范围：${escapeHtml((rule.apply_to || []).join(" / ") || "全部")}；排除：${escapeHtml((rule.exclude || []).join(" / ") || "无")}</p>` : `<h2>选择一条规则</h2><p class="muted">规则传播必须先生成 PatchProposal。</p>`; }
    async function runRevise() { const annotation = selectedAnnotation(); if (!annotation) throw new Error("当前项目没有可处理批注。请先添加批注。 "); const chapterBefore = state.currentFile ? await api("/api/chapters/" + encodeURIComponent(state.currentFile)) : null; const result = await api("/api/skills/run", { method: "POST", body: JSON.stringify({ skill: "revise-with-annotations", annotationIds: [annotation.id], file: annotation.file }) }); state.lastPatch = result.output; if (chapterBefore && annotation.file === state.currentFile) state.currentChapter = chapterBefore; toast("AI 建议已生成 PatchProposal，进入 Diff 审核。 "); await previewPatch(false); setView("diff"); return result; }
    async function previewPatch(showToast = true) { if (!state.lastPatch) await runRevise(); state.lastPreview = await api("/api/patch/preview", { method: "POST", body: JSON.stringify({ patch: state.lastPatch }) }); renderDiffIfReady(); if (showToast) toast("Diff 预览已生成。 "); return state.lastPreview; }
    async function applyPatch() { if (!state.lastPatch) await runRevise(); const result = await api("/api/patch/apply", { method: "POST", body: JSON.stringify({ patch: state.lastPatch }) }); if (!result.applied) throw new Error("Patch 未通过校验，未应用。 "); toast(result.commitError ? "Patch 已应用，但 Git 提交被跳过。" : "接受并提交成功。 "); const current = state.currentFile; state.lastPatch = null; state.lastPreview = null; await loadProjectDetails(false); if (current) await loadChapter(current); setView("editor"); return result; }
    async function manualReviseCurrentBlock() { const chapter = state.currentChapter; if (!chapter) throw new Error("尚未加载章节。 "); const blockId = (chapter.blockIds || [])[0]; const block = chapter.blocks[blockId]; const suffix = block.text.trim() ? "\\n他停了一下，把手里的物件放回原处。" : "他停了一下，把手里的物件放回原处。"; const patch = await api("/api/patch/manual", { method: "POST", body: JSON.stringify({ file: chapter.file, blockId, afterText: `${block.text}${suffix}`, reason: "manual app smoke edit" }) }); state.lastPatch = patch; await previewPatch(false); setView("diff"); toast("已为当前段落生成手动 PatchProposal。 "); }
    function renderDiffIfReady() { const patch = state.lastPatch; if (!patch) { $("rawDiff").textContent = ""; return; } const change = patch.changes?.[0] || {}; const file = change.file || state.currentFile || ""; const blockId = change.targetBlockId || "—"; const source = (patch.sourceAnnotations || [])[0] || "USER"; const before = state.currentChapter?.blocks?.[blockId]?.text || ""; const after = change.afterText || ""; const valid = state.lastPreview?.validation?.valid ?? patch.validation?.valid; const issues = state.lastPreview?.validation?.issues || patch.validation?.issues || []; $("diffTitle").textContent = file || "Patch / Diff 审核"; ["diffBlockTag", "beforeBlockTag", "afterBlockTag", "impactBlock"].forEach((id) => $(id).textContent = blockId); $("sourceAnnotation").textContent = source; $("rulesUsed").textContent = (patch.rulesUsed || [])[0] || "无"; $("patchValidity").textContent = valid === undefined ? "待预览" : (valid ? "通过" : "拒绝"); $("patchValidity").className = valid ? "ok" : "bad"; $("changeReason").textContent = issues.length ? issues.map((i) => `${i.code}: ${i.message}`).join("；") : (change.reason || patch.summary || "根据批注生成建议修改。"); $("rawDiff").textContent = state.lastPreview?.diff || JSON.stringify(patch.safety || {}, null, 2); $("beforeLines").innerHTML = splitLines(before).map((line, idx) => `<div class="diff-line minus"><span class="ln">${idx + 1}</span><span>${escapeHtml(line)}</span><span>−</span></div>`).join(""); $("afterLines").innerHTML = splitLines(after).map((line, idx) => `<div class="diff-line plus"><span class="ln">${idx + 1}</span><span>${escapeHtml(line)}</span><span>＋</span></div>`).join(""); $("changeCheckList").innerHTML = issues.length ? issues.map((issue) => `<div class="check-row"><span>!</span><span>${escapeHtml(issue.message)}</span><strong class="bad">${escapeHtml(issue.code)}</strong></div>`).join("") : `<div class="check-row"><span>▤</span><span>修改的块<br><span class="muted">${escapeHtml(blockId)}</span></span><strong class="ok">✓</strong></div><div class="check-row"><span>✦</span><span>将应用的规则</span><strong class="ok">${escapeHtml((patch.rulesUsed || ["无"])[0] || "无")}</strong></div><div class="check-row"><span>🔒</span><span>锁定章节保持不变</span><strong class="ok">是</strong></div>`; $("commitPreview").textContent = `Apply safe manuscript patch ${patch.id || "runtime-patch"}`; }
    function openProjectModal() { $("projectForm").reset(); $("projectModal").classList.remove("hidden"); setTimeout(() => $("projectTitleInput").focus(), 0); }
    function closeProjectModal() { $("projectModal").classList.add("hidden"); }
    async function submitProject(event) { event.preventDefault(); const payload = Object.fromEntries(new FormData(event.target).entries()); const result = await api("/api/projects/create", { method: "POST", body: JSON.stringify(payload) }); closeProjectModal(); await refreshProjects(); const relativePath = result.summary?.relativePath || result.plan?.slug || payload.slug; if (relativePath) { await openProject(relativePath, { quiet: true }); toast(`已创建并打开项目：${result.summary?.title || result.plan?.title || payload.title}`); } else { toast(`已创建项目：${result.summary?.title || result.plan?.title || payload.title}`); } }
    function selectedBlockIdFromSelection() { const selection = window.getSelection(); const text = selection?.toString().trim(); if (!text || !selection.rangeCount) return null; const node = selection.anchorNode?.nodeType === Node.ELEMENT_NODE ? selection.anchorNode : selection.anchorNode?.parentElement; return node?.closest?.(".paragraph[data-block]")?.dataset.block || null; }
    function openAnnotationModal(blockId) { if (!state.currentChapter) { toast("请先打开一个章节，再添加批注。"); return; } const explicitBlockId = blockId || selectedBlockIdFromSelection(); if (!explicitBlockId) { toast("请先在当前章节中选择一个段落或点击段落旁的“添加批注”。"); return; } const block = state.currentChapter.blocks[explicitBlockId]; if (!block) { toast("未找到可批注的段落，请重新选择。"); return; } const selection = window.getSelection()?.toString().trim(); $("annotationFileInput").value = state.currentChapter.file; $("annotationBlockInput").value = block.id; $("annotationSelectedInput").value = selection && block.text.includes(selection) ? selection : block.text; $("annotationBodyInput").value = ""; $("annotationModal").classList.remove("hidden"); setTimeout(() => $("annotationBodyInput").focus(), 0); }
    function closeAnnotationModal() { $("annotationModal").classList.add("hidden"); }
    async function submitAnnotation(event) { event.preventDefault(); const payload = { file: $("annotationFileInput").value, blockId: $("annotationBlockInput").value, selectedText: $("annotationSelectedInput").value, text: $("annotationBodyInput").value, type: "style", priority: "high" }; const result = await api("/api/annotations/create", { method: "POST", body: JSON.stringify(payload) }); closeAnnotationModal(); await loadProjectDetails(false); if (state.currentFile) await loadChapter(state.currentFile); state.selectedAnnotationId = result.annotation.id; toast("批注已写入 sidecar，正文未被污染。 "); }
    async function submitDiscussion(event) { event.preventDefault(); const payload = { text: $("discussionTextInput").value, file: state.currentFile || "", blockId: state.currentChapter?.blockIds?.[0] || "" }; const result = await api("/api/discussions/create", { method: "POST", body: JSON.stringify(payload) }); state.discussions = [result.discussion, ...(state.discussions || [])]; $("discussionTextInput").value = ""; renderDiscussions(); toast("讨论已写入 sidecar，正文未被修改。 "); await loadProjectDetails(false); setView("discussions"); }
    function bindEvents() { document.querySelectorAll(".nav button").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view))); $("closeProjectModalBtn").addEventListener("click", closeProjectModal); $("cancelProjectBtn").addEventListener("click", closeProjectModal); $("projectForm").addEventListener("submit", (event) => submitProject(event).catch(showError)); $("closeAnnotationModalBtn").addEventListener("click", closeAnnotationModal); $("cancelAnnotationBtn").addEventListener("click", closeAnnotationModal); $("annotationForm").addEventListener("submit", (event) => submitAnnotation(event).catch(showError)); $("discussionForm").addEventListener("submit", (event) => submitDiscussion(event).catch(showError)); $("saveBtn").addEventListener("click", () => toast("Markdown 项目已保持在本地文件中。")); $("generateSuggestionBtn").addEventListener("click", () => runRevise().catch(showError)); $("reviseCurrentBtn").addEventListener("click", () => runRevise().catch(showError)); $("manualPatchBtn").addEventListener("click", () => manualReviseCurrentBlock().catch(showError)); $("addAnnotationGlobalBtn").addEventListener("click", () => openAnnotationModal()); $("addRuleBtn").addEventListener("click", () => setView("rules")); $("laterBtn").addEventListener("click", () => toast("已加入稍后处理列表。")); $("backToEditorBtn").addEventListener("click", () => setView("editor")); $("refreshDiffBtn").addEventListener("click", () => previewPatch().catch(showError)); $("rejectPatchBtn").addEventListener("click", () => { state.lastPatch = null; state.lastPreview = null; toast("已拒绝当前建议，未写入文件。 "); setView("editor"); }); $("partialPatchBtn").addEventListener("click", () => toast("局部应用将在下一阶段支持；当前可接受完整安全 patch。")); $("acceptPatchBtn").addEventListener("click", () => applyPatch().catch(showError)); $("newRuleBtn").addEventListener("click", () => toast("规则创建入口已预留，当前规则由批注提炼生成。")); $("batchApplyBtn").addEventListener("click", () => toast("规则传播会生成 PatchProposal，当前演示保持预览状态。")); $("previewRuleImpactBtn").addEventListener("click", () => toast("已生成规则传播影响预览：draft / unreviewed only。")); $("annotationToEditorBtn").addEventListener("click", () => setView("editor")); $("copyProjectPathBtn").addEventListener("click", async () => { const root = state.project?.root || ""; $("projectPathBox").textContent = root; try { await navigator.clipboard.writeText(root); toast("项目路径已复制。 "); } catch (_) { toast("项目路径已显示。 "); } }); document.addEventListener("keydown", (event) => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); document.querySelector(".search input")?.focus(); } }); window.BookWorkbench = { api, state, loadWorkspace, loadProjectDetails, openProject, runRevise, previewPatch, applyPatch, manualReviseCurrentBlock, submitDiscussion, setView } }
    async function boot() { bindEvents(); await loadWorkspace(); setView("dashboard"); loadHealth().catch(() => { $("settingsCodex").textContent = "未连接"; $("settingsCodex").className = "bad"; }); }
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
        # Keep a lightweight block index sidecar so annotation tests can verify anchoring metadata.
        block_index = {
            file: {bid: {"hash": b.before_hash, "startLine": b.start_line, "endLine": b.end_line} for bid, b in blocks.items()}
            for file, blocks in context.blocks.items()
        }
        (context.root / ".bookai" / "block-index.json").write_text(json.dumps(block_index, ensure_ascii=False, indent=2), encoding="utf-8")
        AuditLog(context.root).append({"type": "annotation.created", "annotationId": annotation_id, "file": file_path, "blockId": block_id})
        if self.runtime is not None:
            self.runtime._reload_context()
        return {"annotation": annotation}

    def _next_annotation_id(self, context) -> str:  # noqa: ANN001 - internal ProjectContext helper
        max_seen = 0
        for annotation in context.annotations:
            if annotation.id.startswith("AN-") and annotation.id[3:].isdigit():
                max_seen = max(max_seen, int(annotation.id[3:]))
        return f"AN-{max_seen + 1:03d}"

    def chapter(self, file_path: str) -> Dict[str, Any]:
        context = self._load_context()
        rel_path = unquote(file_path)
        safe_chapter_path(context.root, rel_path)
        blocks = context.blocks.get(rel_path)
        if blocks is None:
            raise ProjectLoadError(f"Unknown chapter: {rel_path}")
        ordered_blocks = dict(sorted((block_id, asdict(block)) for block_id, block in blocks.items()))
        chapter_title = _markdown_title(safe_chapter_path(context.root, rel_path).read_text(encoding="utf-8"), rel_path)
        return {
            "file": rel_path,
            "title": chapter_title,
            "status": context.status_for_file(rel_path),
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
        print("\nStopping BookWorkbench Local App")
    finally:
        server.server_close()


def _markdown_title(text: str, fallback_path: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or Path(fallback_path).stem
    return Path(fallback_path).stem


def _first(query: Mapping[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


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
