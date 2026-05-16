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
from .patch_engine import validate_patch
from .project import ProjectLoadError, load_project, safe_chapter_path
from .project_creator import ProjectCreationError, create_book_project
from .runtime import RuntimeErrorBase, RuntimeOrchestrator


APP_TITLE = "BookWorkbench Local App"


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Manuscript Workbench</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f9fc;
      --panel: #ffffff;
      --panel-soft: #f8faff;
      --line: #e4e9f3;
      --muted: #75819a;
      --text: #111827;
      --text-soft: #40506a;
      --purple: #5b3df5;
      --purple-2: #7c5cff;
      --purple-soft: #eeeafe;
      --green: #4caf67;
      --green-soft: #eaf8ee;
      --orange: #f58228;
      --orange-soft: #fff0e4;
      --red: #ec5b62;
      --red-soft: #fff0f1;
      --nav: #071224;
      --nav-soft: #111d32;
      --shadow: 0 18px 48px rgba(15, 23, 42, .08);
      --radius: 18px;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--text); }
    button, input, textarea, select { font: inherit; }
    button { cursor: pointer; }
    .app { min-height: 100vh; display: grid; grid-template-columns: 304px 1fr; }
    .sidebar {
      position: sticky; top: 0; height: 100vh; padding: 28px 24px;
      display: flex; flex-direction: column; gap: 22px;
      background:
        radial-gradient(circle at 18% 0%, rgba(100, 80, 255, .26), transparent 28%),
        linear-gradient(180deg, #071224 0%, #050d19 100%);
      color: #eaf0ff;
    }
    .brand { display: flex; gap: 14px; align-items: center; margin-bottom: 10px; }
    .logo {
      width: 52px; height: 52px; border-radius: 12px; display: grid; place-items: center;
      background: linear-gradient(135deg, #6c55ff, #1c1d68); box-shadow: 0 8px 22px rgba(91, 61, 245, .35);
      font-size: 29px;
    }
    .brand strong { display: block; font-size: 19px; letter-spacing: -.02em; }
    .brand span { display: block; color: #93a4c0; margin-top: 4px; font-size: 14px; }
    .nav { display: grid; gap: 8px; }
    .nav button {
      border: 0; width: 100%; color: #dbe6fb; background: transparent; text-align: left;
      display: flex; align-items: center; gap: 14px; padding: 14px 16px; border-radius: 12px;
      font-size: 17px;
    }
    .nav button:hover { background: rgba(255,255,255,.08); }
    .nav button.active { background: linear-gradient(90deg, rgba(100, 80, 255, .72), rgba(85, 64, 190, .58)); color: #fff; }
    .nav .icon { width: 22px; text-align: center; font-size: 20px; }
    .sidebar-spacer { flex: 1; }
    .book-card, .help-card {
      border: 1px solid rgba(255,255,255,.06); border-radius: 14px; background: rgba(255,255,255,.07);
      padding: 14px; box-shadow: 0 12px 30px rgba(0,0,0,.18);
    }
    .book-card { display: flex; gap: 12px; align-items: center; }
    .cover {
      width: 58px; height: 58px; border-radius: 10px;
      background:
        linear-gradient(rgba(0,0,0,.05), rgba(0,0,0,.2)),
        url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='80' height='80'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0' x2='1' y1='0' y2='1'%3E%3Cstop stop-color='%23101b33'/%3E%3Cstop offset='1' stop-color='%230b6380'/%3E%3C/linearGradient%3E%3C/defs%3E%3Crect width='80' height='80' fill='url(%23g)'/%3E%3Cpath d='M8 58 C25 45 31 66 49 51 S68 45 76 31' stroke='%239ccfff' stroke-width='3' fill='none' opacity='.55'/%3E%3Ccircle cx='58' cy='22' r='10' fill='%23f7f0bb' opacity='.7'/%3E%3C/svg%3E") center/cover;
    }
    .book-card strong { display: block; font-size: 17px; }
    .book-card span, .storage span, .help-card { color: #aebbd3; font-size: 13px; }
    .storage { display: grid; gap: 10px; padding: 0 14px; }
    .storage-row { display: flex; justify-content: space-between; }
    .bar { height: 8px; border-radius: 999px; background: rgba(255,255,255,.16); overflow: hidden; }
    .bar > i { display: block; height: 100%; width: 16%; border-radius: inherit; background: linear-gradient(90deg, #6e55ff, #a08dff); }
    .help-card { display: flex; justify-content: space-between; align-items: center; padding: 16px; }

    .main { min-width: 0; padding: 30px 34px 26px; }
    .topbar { display: flex; align-items: center; gap: 20px; margin-bottom: 26px; }
    .title-wrap { flex: 1; min-width: 0; }
    h1 { margin: 0; font-size: 30px; letter-spacing: -.035em; line-height: 1.15; }
    .subtitle { margin-top: 9px; color: var(--muted); font-size: 14px; }
    .search {
      width: 390px; height: 48px; border: 1px solid #d9e1ef; background: #fff; border-radius: 14px;
      display: flex; align-items: center; gap: 10px; padding: 0 14px; color: var(--muted);
      box-shadow: 0 8px 24px rgba(15,23,42,.04);
    }
    .search input { border: 0; outline: 0; flex: 1; min-width: 0; color: var(--text); }
    .kbd { border: 1px solid var(--line); border-radius: 8px; padding: 2px 7px; background: #f3f6fb; color: #8b95a8; }
    .bell { position: relative; width: 42px; height: 42px; border-radius: 50%; display: grid; place-items: center; color: #65718c; font-size: 22px; }
    .badge { position: absolute; right: 4px; top: 2px; background: var(--purple); color: #fff; border-radius: 999px; font-size: 11px; padding: 2px 6px; }
    .avatar { display: flex; align-items: center; gap: 10px; color: #101828; font-weight: 650; }
    .avatar-img { width: 46px; height: 46px; border-radius: 50%; background: radial-gradient(circle at 45% 30%, #f5d7c8, #3f475c 38%, #0d111b 65%); }

    .grid { display: grid; gap: 18px; }
    .dashboard { grid-template-columns: minmax(0, 1fr) 390px; align-items: start; }
    .editor, .diff, .rules-layout { grid-template-columns: minmax(0, 1fr) 390px; align-items: start; }
    .stats { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 16px; }
    .card {
      background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow);
    }
    .metric { padding: 18px; min-height: 118px; display: grid; grid-template-columns: 54px 1fr; gap: 12px; align-items: center; }
    .metric-icon, .small-icon {
      width: 48px; height: 48px; border-radius: 16px; display: grid; place-items: center; font-size: 24px;
    }
    .metric-icon.purple, .small-icon.purple { background: var(--purple-soft); color: var(--purple); }
    .metric-icon.green, .small-icon.green { background: var(--green-soft); color: var(--green); }
    .metric-icon.blue, .small-icon.blue { background: #eaf2ff; color: #3f7fe8; }
    .metric-icon.orange, .small-icon.orange { background: var(--orange-soft); color: var(--orange); }
    .metric .label { color: var(--muted); font-size: 14px; }
    .metric .num { display: block; margin: 4px 0; font-size: 30px; font-weight: 760; letter-spacing: -.03em; }
    .metric .note { color: #7b86a0; font-size: 13px; }
    .panel { padding: 18px; }
    .panel-title { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 16px; }
    .panel-title h2, .panel-title h3 { margin: 0; font-size: 18px; }
    .link-btn { border: 0; background: transparent; color: var(--purple); font-weight: 650; }
    .progress-card { display: grid; grid-template-columns: 190px 170px 1fr; gap: 18px; min-height: 300px; }
    .ring {
      width: 150px; height: 150px; border-radius: 50%; margin: 42px auto 8px; display: grid; place-items: center;
      background: conic-gradient(var(--purple) 0 68%, #eef1f6 68% 100%);
      position: relative;
    }
    .ring::after { content: ""; position: absolute; inset: 13px; border-radius: 50%; background: #fff; }
    .ring strong { position: relative; z-index: 1; font-size: 33px; }
    .progress-stats { align-self: center; display: grid; gap: 18px; color: var(--text-soft); }
    .progress-stats strong { color: var(--text); font-size: 18px; }
    .chart { position: relative; min-height: 230px; padding: 34px 16px 16px; overflow: hidden; }
    .chart-grid { position: absolute; inset: 42px 8px 34px 10px; background: repeating-linear-gradient(to bottom, transparent, transparent 48px, #e9edf5 49px); }
    .chart svg { position: relative; z-index: 1; width: 100%; height: 220px; }
    .today { position: absolute; left: 64%; bottom: 46px; z-index: 2; background: var(--purple); color: #fff; border-radius: 8px; padding: 4px 9px; font-size: 12px; }
    .chapter-table { width: 100%; border-collapse: collapse; font-size: 14px; }
    .chapter-table th { color: #7b86a0; text-align: left; font-weight: 600; padding: 12px 10px; border-bottom: 1px solid var(--line); }
    .chapter-table td { padding: 12px 10px; border-bottom: 1px solid #edf1f6; }
    .chapter-table tr:hover { background: #fafbff; }
    .status-pill { display: inline-flex; align-items: center; gap: 5px; border-radius: 8px; padding: 4px 8px; font-size: 12px; font-weight: 650; }
    .status-draft { background: var(--orange-soft); color: var(--orange); }
    .status-reviewed { background: var(--green-soft); color: var(--green); }
    .status-locked { background: #eaf2ff; color: #3f7fe8; }
    .status-unreviewed { background: var(--purple-soft); color: var(--purple); }
    .mini-progress { height: 5px; border-radius: 999px; background: #e5eaf2; min-width: 70px; overflow: hidden; }
    .mini-progress i { display: block; height: 100%; background: var(--purple); border-radius: inherit; }
    .side-stack { display: grid; gap: 16px; }
    .suggestion { display: grid; grid-template-columns: 48px 1fr 18px; gap: 12px; align-items: center; border: 1px solid var(--line); border-radius: 12px; padding: 12px; margin-bottom: 10px; }
    .suggestion strong { display: block; margin-bottom: 4px; }
    .suggestion span { color: var(--text-soft); font-size: 13px; }
    .health-score { display: grid; grid-template-columns: 140px 1fr; gap: 10px; align-items: center; }
    .score-ring { width: 112px; height: 112px; border-radius: 50%; display: grid; place-items: center; background: conic-gradient(var(--green) 0 82%, #edf2f6 82% 100%); position: relative; }
    .score-ring::after { content: ""; position: absolute; inset: 10px; border-radius: 50%; background: #fff; }
    .score-ring div { position: relative; z-index: 1; text-align: center; }
    .score-ring strong { display: block; font-size: 32px; }
    .quick-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
    .quick-grid button, .ghost-btn, .primary-btn, .danger-btn {
      min-height: 46px; border-radius: 10px; border: 1px solid #dfe5f0; background: #fff; color: var(--text-soft); font-weight: 650;
    }
    .primary-btn { border: 0; color: #fff; background: linear-gradient(135deg, var(--purple-2), var(--purple)); box-shadow: 0 10px 22px rgba(91,61,245,.22); }
    .danger-btn { color: var(--red); border-color: #ffb9bf; background: #fff; }
    .ghost-btn:hover, .quick-grid button:hover { border-color: #b9c4d7; background: #fafbff; }

    .toolbar { min-height: 76px; padding: 14px 16px; display: flex; align-items: center; gap: 14px; border-bottom: 1px solid var(--line); }
    .select-like { border: 1px solid #dfe5f0; border-radius: 10px; padding: 10px 14px; background: #fff; min-width: 190px; font-weight: 700; }
    .toolbar .spacer { flex: 1; }
    .editor-card { overflow: hidden; }
    .doc { padding: 42px 44px 24px; background: #fff; min-height: 700px; }
    .doc h2 { font-size: 25px; margin: 0 0 28px; }
    .paragraph { display: grid; grid-template-columns: 90px minmax(0, 1fr) 72px; gap: 18px; align-items: start; margin: 22px 0; line-height: 2; font-size: 17px; }
    .pid { color: #7d8aa4; font-size: 13px; padding-top: 8px; }
    .ptext { padding: 3px 6px; border-radius: 8px; white-space: pre-wrap; }
    .paragraph.annotated .ptext { background: linear-gradient(90deg, rgba(101,80,255,.16), rgba(101,80,255,.05)); }
    .tag { display: inline-flex; align-items: center; justify-content: center; min-width: 58px; padding: 3px 8px; border-radius: 7px; background: var(--purple-soft); color: var(--purple); font-size: 13px; font-weight: 750; }
    .editor-footer { display: flex; gap: 24px; align-items: center; border-top: 1px solid var(--line); padding: 14px 24px; color: #6b7892; font-size: 14px; }
    .annotation-card { border: 1px solid var(--line); border-radius: 14px; padding: 14px; margin-bottom: 12px; background: #fff; }
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
    .diff-col { padding: 18px; background: #fff; border: 1px solid var(--line); border-radius: 14px; min-height: 520px; }
    .diff-col h3 { margin: 0 0 14px; }
    .diff-line { display: grid; grid-template-columns: 34px 1fr 18px; gap: 10px; padding: 10px; border-radius: 8px; margin: 7px 0; line-height: 1.7; }
    .diff-line.minus { background: var(--red-soft); }
    .diff-line.plus { background: var(--green-soft); }
    .diff-line.neutral { background: #fff; }
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
    .rule-row { border: 1px solid transparent; border-bottom-color: #edf1f6; border-radius: 12px; padding: 14px; display: grid; gap: 8px; }
    .rule-row.active { border-color: var(--purple); box-shadow: 0 10px 24px rgba(91,61,245,.08); }
    .rule-row .rule-meta { color: #71809a; font-size: 13px; display: flex; justify-content: space-between; }
    .rule-detail { padding: 28px; }
    .rule-detail h2 { margin: 16px 0 12px; font-size: 20px; line-height: 1.5; }
    .detail-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); margin: 26px 0; }
    .detail-stats div { padding: 15px 12px; border-right: 1px solid var(--line); }
    .detail-stats div:last-child { border-right: 0; }
    .propagate { display: grid; grid-template-columns: 1fr 1fr 180px; gap: 14px; align-items: start; }
    .check-card { border: 1px solid var(--line); border-radius: 14px; padding: 16px; }
    .check-card label { display: block; margin: 12px 0; color: var(--text-soft); }
    .impact-card { display: grid; gap: 14px; }

    .hidden { display: none !important; }
    .toast { position: fixed; right: 24px; bottom: 24px; background: #0f172a; color: #fff; padding: 13px 16px; border-radius: 12px; box-shadow: 0 16px 38px rgba(15,23,42,.28); z-index: 20; opacity: 0; transform: translateY(10px); transition: .18s ease; max-width: min(460px, calc(100vw - 48px)); }
    .toast.show { opacity: 1; transform: translateY(0); }
    .empty { color: var(--muted); padding: 18px; border: 1px dashed #d7deeb; border-radius: 12px; background: #fbfcff; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    .ok { color: var(--green); }
    .bad { color: var(--red); }
    .muted { color: var(--muted); }
    .mt { margin-top: 16px; }

    @media (max-width: 1160px) {
      .app { grid-template-columns: 88px 1fr; }
      .sidebar { padding: 20px 14px; }
      .brand div:not(.logo), .nav span:not(.icon), .book-card, .storage, .help-card { display: none; }
      .nav button { justify-content: center; padding: 14px; }
      .main { padding: 24px; }
      .dashboard, .editor, .diff, .rules-layout, .rules-main, .progress-card, .propagate { grid-template-columns: 1fr; }
      .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .search { width: 300px; }
    }
    @media (max-width: 760px) {
      .app { display: block; }
      .sidebar { position: static; height: auto; }
      .nav { grid-template-columns: repeat(3, 1fr); }
      .nav button { font-size: 13px; }
      .topbar { flex-wrap: wrap; }
      .search { order: 3; width: 100%; }
      .stats, .diff-columns, .change-meta, .detail-stats { grid-template-columns: 1fr; }
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
        <button data-view="editor"><span class="icon">▤</span><span>文稿</span></button>
        <button data-view="annotations"><span class="icon">☵</span><span>批注</span></button>
        <button data-view="rules"><span class="icon">▱</span><span>规则</span></button>
        <button data-view="diff"><span class="icon">↺</span><span>版本</span></button>
        <button data-view="export"><span class="icon">⇱</span><span>导出</span></button>
        <button data-view="settings"><span class="icon">⚙</span><span>设置</span></button>
      </nav>
      <div class="sidebar-spacer"></div>
      <div class="book-card"><div class="cover"></div><div><strong id="sideBookTitle">《黑雨之后》</strong><span id="sideBookMeta">长篇小说 · 进行中</span></div><span>⌄</span></div>
      <div class="storage"><div class="storage-row"><span>存储空间</span><span>12.4 GB / 100 GB</span></div><div class="bar"><i></i></div></div>
      <div class="help-card"><span>ⓘ 帮助与反馈</span><span>›</span></div>
    </aside>

    <main class="main">
      <header class="topbar">
        <div class="title-wrap"><h1 id="pageTitle">《黑雨之后》</h1><div id="pageSubtitle" class="subtitle">长篇小说 · 创建于 2024-11-10 · 正在连接本地 Runtime 与 Codex app-server…</div></div>
        <label class="search">⌕ <input placeholder="搜索章节、文稿、批注..." aria-label="搜索"><span class="kbd">⌘K</span></label>
        <div class="bell">♧<span class="badge">3</span></div>
        <div class="avatar"><div class="avatar-img"></div><span>林默⌄</span></div>
      </header>

      <section id="view-dashboard" class="view grid dashboard">
        <div class="grid">
          <div class="stats" id="stats"></div>
          <section class="card panel">
            <div class="panel-title"><h2>写作进度 ⓘ</h2><span class="muted">— 已写字数 · · · 目标字数</span></div>
            <div class="progress-card">
              <div><div class="ring"><strong>68%</strong></div><p class="muted" style="text-align:center">整体进度</p></div>
              <div class="progress-stats">
                <div><span class="muted">已写字数</span><br><strong id="writtenWords">0</strong><br><span class="muted">目标 520,000</span></div>
                <div><span class="muted">剩余字数</span><br><strong>163,218</strong></div>
                <div><span class="muted">预计完成</span><br><strong>2025-07-18</strong><br><span class="muted">（基于当前速度）</span></div>
              </div>
              <div class="chart"><div class="chart-grid"></div><svg viewBox="0 0 420 220" preserveAspectRatio="none"><defs><linearGradient id="fill" x1="0" x2="0" y1="0" y2="1"><stop stop-color="#6c55ff" stop-opacity=".24"/><stop offset="1" stop-color="#6c55ff" stop-opacity="0"/></linearGradient></defs><path d="M0,190 L30,172 L62,160 L94,142 L126,134 L160,112 L190,100 L220,76 L252,70 L286,60 L318,58 L318,190 Z" fill="url(#fill)"/><path d="M0,190 L30,172 L62,160 L94,142 L126,134 L160,112 L190,100 L220,76 L252,70 L286,60 L318,58" stroke="#6c55ff" stroke-width="4" fill="none" stroke-linecap="round"/><path d="M318,58 L318,190 M318,58 L420,10" stroke="#6c55ff" stroke-width="2" stroke-dasharray="5 7" fill="none"/></svg><span class="today">今天</span></div>
            </div>
          </section>
          <section class="card panel">
            <div class="panel-title"><h2>章节进度</h2><button class="ghost-btn" id="refreshProjectBtn">↕ 排序</button></div>
            <table class="chapter-table"><thead><tr><th>□</th><th>章节</th><th>状态</th><th>字数</th><th>更新时间</th><th>进度</th><th></th></tr></thead><tbody id="chapterRows"></tbody></table>
          </section>
        </div>
        <aside class="side-stack">
          <section class="card panel"><div class="panel-title"><h3>✦ AI 建议</h3><button class="link-btn" id="rerunBtn">换一换 ↻</button></div><div id="aiSuggestions"></div><p class="muted">由 Manuscript AI 生成 ⓘ</p></section>
          <section class="card panel"><div class="panel-title"><h3>项目健康度</h3></div><div class="health-score"><div class="score-ring"><div><strong>82</strong><span class="ok">良好</span></div></div><div id="healthList"></div></div><p class="muted">基于当前文稿分析 · 1 小时前更新</p></section>
          <section class="card panel"><div class="panel-title"><h3>快捷操作</h3></div><div class="quick-grid"><button id="newProjectBtn">⊕ 新建项目</button><button id="openEditorBtn">▤ 打开文稿</button><button id="newAnnotationBtn">☵ 处理批注</button><button id="exportBtn">⇩ 导出项目</button></div></section>
        </aside>
      </section>

      <section id="view-editor" class="view grid editor hidden">
        <div class="card editor-card">
          <div class="toolbar"><div class="select-like" id="chapterSelect">第5章 证据链⌄</div><span class="status-pill status-draft" id="editorStatus">草稿</span><span class="muted" id="editorWords">字数：0</span><div class="spacer"></div><button class="ghost-btn" id="saveBtn">✓ 保存</button><button class="primary-btn" id="generateSuggestionBtn">✦ 生成建议</button><button class="ghost-btn" id="moreBtn">···</button></div>
          <article class="doc" id="docView"></article>
          <footer class="editor-footer"><span id="chapterWordCount">本章字数：0</span><span>预计阅读时间：28 分钟</span><span>内容健康度：良好 <span class="ok">●</span></span><span class="spacer"></span><span id="selectionInfo">1,238 字</span></footer>
        </div>
        <aside class="side-stack">
          <section class="card panel"><div class="panel-title"><h3>批注与 AI 建议</h3><button class="ghost-btn" id="filterBtn">全部⌄</button></div><div class="tabs"><button class="tab active">批注 (<span id="annotationCount">0</span>)</button><button class="tab">AI 建议 (8)</button></div><div id="annotationPanel" class="mt"></div></section>
          <section class="card panel"><div class="panel-title"><h3>AI 解读与建议 ⓘ</h3><span class="muted">基于上下文分析</span></div><div id="aiActionList"></div><div class="button-row"><button class="primary-btn" id="reviseCurrentBtn">仅修改当前段落</button><button class="ghost-btn" id="addRuleBtn">加入规则库</button></div><div class="button-row single"><button class="ghost-btn" id="laterBtn">稍后处理</button></div></section>
          <section class="card panel"><div class="stats" style="grid-template-columns:repeat(3,1fr); gap:8px"><div><strong>87%</strong><br><span class="muted">本章规则匹配</span></div><div><strong class="bad" id="pendingAnnotationCount">0</strong><br><span class="muted">待处理批注</span></div><div><strong class="ok">✓</strong><br><span class="muted">上次保存</span></div></div></section>
        </aside>
      </section>

      <section id="view-diff" class="view grid diff hidden">
        <div>
          <div class="diff-top"><button class="back-btn" id="backToEditorBtn">‹</button><h1 style="font-size:24px" id="diffTitle">第5章 证据链</h1><span class="tag" id="diffBlockTag">ch05-p018</span><div class="spacer"></div><button class="ghost-btn">‹ 上一个建议</button><button class="ghost-btn">下一个建议 ›</button></div>
          <div class="card change-meta"><div class="meta-cell"><span class="small-icon purple">☵</span><div><span class="muted">来源批注</span><br><strong id="sourceAnnotation">AN-041</strong></div></div><div class="meta-cell"><span class="small-icon purple">✦</span><div><span class="muted">应用规则</span><br><strong id="rulesUsed">R-018</strong></div></div><div class="meta-cell"><span class="small-icon blue">✦</span><div><span class="muted">模型</span><br><strong>Codex</strong></div></div><div class="meta-cell"><span class="small-icon green">✓</span><div><span class="muted">置信度</span><br><strong>0.89</strong></div></div></div>
          <div class="diff-columns"><div class="diff-col"><h3>原文（修改前） <span class="tag" id="beforeBlockTag">ch05-p018</span></h3><div id="beforeLines"></div></div><div class="diff-col"><h3>建议（修改后） <span class="tag" id="afterBlockTag">ch05-p018</span></h3><div id="afterLines"></div></div></div>
          <section class="card diff-reason"><div class="panel-title"><h3>ⓘ 修改原因</h3><button class="link-btn">⌃</button></div><p id="changeReason" class="muted">根据批注，减少直接心理解释，改为动作与停顿来呈现人物内心变化。</p><pre class="diff-raw" id="rawDiff"></pre></section>
        </div>
        <aside class="side-stack"><div class="button-row" style="grid-template-columns:1fr 1fr 1fr"><button class="danger-btn" id="rejectPatchBtn">拒绝</button><button class="ghost-btn" id="partialPatchBtn">部分应用</button><button class="primary-btn" id="acceptPatchBtn">接受并提交</button></div><section class="card panel"><div class="panel-title"><h3>本次变更</h3></div><div class="check-list" id="changeCheckList"></div><h3 class="mt">Git 提交预览</h3><div class="commit-box" id="commitPreview">edit(ch05): apply AN-041 and R-018</div></section><section class="card panel"><div class="panel-title"><h3>影响范围</h3></div><div class="check-list"><div class="check-row"><span>▤</span><span>当前段落</span><strong id="impactBlock">ch05-p018</strong></div><div class="check-row"><span>⊕</span><span>当前章节：仅建议</span><strong>未直接应用到文件 ›</strong></div><div class="check-row"><span>⊕</span><span>其他章节：不修改</span><strong>0 个章节受影响 ›</strong></div></div></section><section class="card panel"><div class="panel-title"><h3>版本与分支</h3></div><div class="check-list"><div class="check-row"><span>⌘</span><span>当前分支</span><strong>main</strong></div><div class="check-row"><span>◷</span><span>提交前预览</span><strong>1 个提交将在接受后生成 ›</strong></div></div></section></aside>
      </section>

      <section id="view-rules" class="view rules-layout hidden">
        <div class="rules-main">
          <section class="card"><div class="toolbar"><button class="primary-btn" id="newRuleBtn">＋ 新建规则</button><button class="ghost-btn">≋ 筛选</button><button class="ghost-btn" id="batchApplyBtn">▣ 批量应用</button></div><div class="tabs"><button class="tab active">全部（<span id="ruleCount">0</span>）</button><button class="tab">风格</button><button class="tab">结构</button><button class="tab">设定</button><button class="tab">节奏</button></div><div class="rule-list" id="ruleList"></div></section>
          <div class="grid"><section class="card rule-detail" id="ruleDetail"></section><section class="propagate"><div class="card check-card"><h3>目标状态</h3><label><input type="checkbox" checked> 草稿 (draft)<br><span class="muted">将应用到草稿章节</span></label><label><input type="checkbox" checked> 未审阅 (unreviewed)<br><span class="muted">将应用到未审阅章节</span></label><label class="muted"><input type="checkbox" disabled> 已审阅 (reviewed) 🔒</label><label class="muted"><input type="checkbox" disabled> 已锁定 (locked) 🔒</label></div><div class="card check-card"><h3>目标章节预览</h3><label><input type="checkbox" checked> ch06 第06章 笔计时 · 3,842字</label><label><input type="checkbox" checked> ch07 第07章 潮汐线 · 4,126字</label><label><input type="checkbox" checked> ch08 第08章 低语 · 5,013字</label><label class="muted"><input type="checkbox" disabled> ch09 第09章 旧的</label><div class="button-row single"><button class="primary-btn" id="previewRuleImpactBtn">预览影响 / 应用到目标章节</button></div></div><aside class="side-stack"><section class="card panel impact-card"><h3>影响预览</h3><div><strong>预计修改</strong><br><span>18 个段落</span></div><div><strong>不触碰</strong><br><span>已锁定内容</span></div><div><strong>生成 patch</strong><br><span>供审阅</span></div></section></aside></section></div>
        </div>
      </section>

      <section id="view-annotations" class="view hidden"><section class="card panel"><div class="panel-title"><h2>批注中心</h2><button class="primary-btn" id="annotationToEditorBtn">打开文稿处理</button></div><div id="annotationCenter"></div></section></section>
      <section id="view-export" class="view hidden"><section class="card panel"><h2>导出项目</h2><p class="muted">导出适配器在设计稿中已预留。当前 MVP 保持 Markdown 原生项目结构，可直接复制项目目录。</p><button class="ghost-btn" id="copyProjectPathBtn">复制项目路径</button><pre class="diff-raw" id="projectPathBox"></pre></section></section>
      <section id="view-settings" class="view hidden"><section class="card panel"><h2>设置 / 安全中心</h2><div class="check-list"><div class="check-row"><span>🛡</span><span>AI 只生成建议，不自动写入</span><strong class="ok">开启</strong></div><div class="check-row"><span>🔒</span><span>锁定章节禁止修改</span><strong class="ok">开启</strong></div><div class="check-row"><span>▣</span><span>修改必须先生成 PatchProposal</span><strong class="ok">开启</strong></div><div class="check-row"><span>⌘</span><span>Codex app-server 连接</span><strong id="settingsCodex">检测中</strong></div></div></section></section>
    </main>
  </div>

  <div class="toast" id="toast" role="status" aria-live="polite"></div>

  <script>
    const BOOKWORKBENCH_TOKEN = __BOOKWORKBENCH_TOKEN__;
    const state = {
      project: null,
      annotations: [],
      audit: [],
      currentFile: null,
      currentChapter: null,
      lastPatch: null,
      lastPreview: null,
      activeView: "dashboard"
    };
    const $ = (id) => document.getElementById(id);
    function escapeHtml(value) {
      const span = document.createElement("span");
      span.textContent = String(value ?? "");
      return span.innerHTML;
    }
    function toast(message) {
      const el = $("toast");
      el.textContent = message;
      el.classList.add("show");
      clearTimeout(toast.timer);
      toast.timer = setTimeout(() => el.classList.remove("show"), 2600);
    }
    async function api(path, options = {}) {
      const headers = {
        "Content-Type": "application/json",
        "X-BookWorkbench-Token": BOOKWORKBENCH_TOKEN,
        ...(options.headers || {})
      };
      const response = await fetch(path, { ...options, headers });
      const text = await response.text();
      let payload;
      try { payload = text ? JSON.parse(text) : {}; } catch (_) { payload = { raw: text }; }
      if (!response.ok) throw new Error(payload.error || response.statusText);
      return payload;
    }
    function setView(view) {
      const actual = view === "annotations" ? "annotations" : view;
      state.activeView = actual;
      document.querySelectorAll(".view").forEach((el) => el.classList.add("hidden"));
      const target = $(`view-${actual}`);
      if (target) target.classList.remove("hidden");
      document.querySelectorAll(".nav button").forEach((btn) => btn.classList.toggle("active", btn.dataset.view === view || btn.dataset.view === actual));
      const titles = {
        dashboard: [bookTitle(), "长篇小说 · 创建于 2024-11-10 · 最后更新：刚刚"],
        editor: ["文稿编辑", `项目 / ${bookTitle()} / 文稿`],
        annotations: ["批注中心", "集中处理开放批注与 AI 建议"],
        diff: ["Patch / Diff 审核", "所有修改先预览，再由 Runtime 安全应用"],
        rules: ["规则中心", "从作者批注中沉淀长期写作偏好与约束"],
        export: ["导出项目", "Markdown 项目结构可直接复制或进入导出适配器"],
        settings: ["设置 / 安全中心", "安全边界、Codex 连接与审计策略"]
      };
      const [title, subtitle] = titles[actual] || titles.dashboard;
      $("pageTitle").textContent = title;
      $("pageSubtitle").textContent = subtitle;
      if (actual === "diff" && !state.lastPatch) runRevise().catch(showError);
    }
    function showError(error) {
      console.error(error);
      toast(String(error.message || error));
      $("pageSubtitle").innerHTML = `<span class="bad">发生错误：</span>${escapeHtml(error.message || error)}`;
    }
    function bookTitle() {
      const spec = state.project?.bookSpec || "";
      const match = spec.match(/#\s*(.+?)\s*Book SPEC/);
      return match ? match[1].trim() : "《黑雨之后》";
    }
    function chapterTitleFromText(text, fallback) {
      const match = String(text || "").match(/^#\s+(.+)$/m);
      return match ? match[1].trim() : fallback;
    }
    function wordCount(text) {
      const compact = String(text || "").replace(/<!--.*?-->/gs, "").replace(/\s+/g, "");
      return compact.length;
    }
    function statusLabel(status) {
      const map = { draft: "草稿", reviewed: "已审阅", locked: "已锁定", unreviewed: "未审阅" };
      return map[status] || status || "草稿";
    }
    function statusClass(status) { return `status-${status || "draft"}`; }
    function firstChapterFile() {
      const files = Object.keys(state.project?.blocks || {});
      return files.includes("chapters/ch05.md") ? "chapters/ch05.md" : files[0];
    }
    function blockAnnotation(blockId) {
      return state.annotations.find((item) => item.block_id === blockId || item.blockId === blockId);
    }
    async function loadHealth() {
      const health = await api("/api/health");
      const codexOk = !!health.codex?.ok;
      const runtimeOk = !!health.runtime?.ok;
      const codexText = codexOk ? "Codex app-server OK" : "Codex app-server 未连接";
      $("pageSubtitle").innerHTML = `Runtime <span class="ok">${runtimeOk ? "OK" : "未连接"}</span> · <span class="${codexOk ? "ok" : "bad"}">${codexText}</span>`;
      $("settingsCodex").textContent = codexOk ? "OK" : "未连接";
      $("settingsCodex").className = codexOk ? "ok" : "bad";
      return health;
    }
    async function loadProject() {
      const [project, annotationPayload, auditPayload] = await Promise.all([
        api("/api/project"),
        api("/api/annotations?include_resolved=1"),
        api("/api/audit")
      ]);
      state.project = project;
      state.annotations = annotationPayload.annotations || [];
      state.audit = auditPayload.events || [];
      state.currentFile = state.currentFile || firstChapterFile();
      $("sideBookTitle").textContent = bookTitle();
      renderDashboard();
      renderRules();
      renderAnnotations();
      await loadChapter(state.currentFile);
      return project;
    }
    async function loadChapter(file) {
      if (!file) return;
      state.currentFile = file;
      state.currentChapter = await api("/api/chapters/" + encodeURIComponent(file));
      renderEditor();
      return state.currentChapter;
    }
    function renderDashboard() {
      const files = Object.keys(state.project?.blocks || {});
      const annotations = state.annotations;
      const statuses = state.project?.chapterStatus || {};
      const reviewed = Object.values(statuses).filter((s) => s === "reviewed").length;
      const locked = Object.values(statuses).filter((s) => s === "locked").length;
      const open = annotations.filter((a) => a.status === "open").length;
      const rules = state.project?.rules || [];
      const stats = [
        ["📖", "总章节数", Math.max(files.length, 48), "规划中 2 章", "purple"],
        ["✓", "已审阅", Math.max(reviewed, 28), "58.3%", "green"],
        ["🔒", "已锁定", Math.max(locked, 16), "33.3%", "blue"],
        ["☵", "待处理批注", Math.max(open, 37), "较昨日 -5", "orange"],
        ["✦", "活跃规则", Math.max(rules.length, 12), "匹配率 89%", "purple"]
      ];
      $("stats").innerHTML = stats.map(([icon, label, num, note, color]) => `<div class="card metric"><div class="metric-icon ${color}">${icon}</div><div><span class="label">${label}</span><strong class="num">${num}</strong><span class="note">${note}</span></div></div>`).join("");
      const chapterRows = buildChapterRows(files, statuses);
      $("chapterRows").innerHTML = chapterRows.map((row, idx) => `<tr data-file="${escapeHtml(row.file)}"><td>□</td><td><strong>第${idx + 1}章&nbsp; ${escapeHtml(row.title)}</strong></td><td><span class="status-pill ${statusClass(row.status)}">${statusLabel(row.status)}</span></td><td>${row.words.toLocaleString()}</td><td>${row.updated}</td><td><span>${row.progress}%</span></td><td><div class="mini-progress"><i style="width:${row.progress}%"></i></div></td></tr>`).join("");
      document.querySelectorAll("#chapterRows tr").forEach((tr) => tr.addEventListener("click", () => {
        if (!state.project?.blocks?.[tr.dataset.file]) { toast("该章节是设计稿中的规划行，当前示例项目尚未创建正文文件。"); return; }
        loadChapter(tr.dataset.file).then(() => setView("editor")).catch(showError);
      }));
      $("writtenWords").textContent = chapterRows.reduce((sum, item) => sum + item.words, 0).toLocaleString();
      $("aiSuggestions").innerHTML = [
        ["📖", "补充角色动机", "“林默”在第3-4章的行为转变较突兀，建议补充其内心动机或外部触发事件。", "purple"],
        ["▤", "时间线检查", "第12章与第13章的时间间隔可能过短，建议核对时间线逻辑。", "blue"],
        ["☵", "批注处理建议", `当前有 ${Math.max(open, 37)} 条待处理批注，建议优先处理高优先级批注。`, "orange"]
      ].map(([icon, title, text, color]) => `<div class="suggestion"><span class="small-icon ${color}">${icon}</span><div><strong>${title}</strong><span>${text}</span></div><span>›</span></div>`).join("");
      $("healthList").innerHTML = ["结构完整性 良好", "内容一致性 良好", "风格稳定性 中等", "设定遵循度 良好"].map((item) => `<p>${escapeHtml(item)} <span class="ok">●</span></p>`).join("");
    }
    function buildChapterRows(files, statuses) {
      const defaults = [
        ["chapters/ch01.md", "雨夜开端", "locked", 8732, 100],
        ["chapters/ch02.md", "旧案重提", "locked", 9105, 100],
        ["chapters/ch03.md", "失踪者", "reviewed", 7945, 100],
        ["chapters/ch04.md", "暗流", "reviewed", 8112, 100],
        ["chapters/ch05.md", "证据链", "draft", 6721, 68],
        ["chapters/ch06.md", "隐藏的名字", "draft", 4238, 42]
      ];
      const rows = defaults.map(([file, title, status, words, progress], idx) => ({ file, title, status: statuses[file] || status, words, progress, updated: idx < 2 ? "2025-05-19 22:31" : "2025-05-20 09:15" }));
      files.forEach((file) => {
        if (!rows.some((row) => row.file === file)) rows.push({ file, title: file.split("/").pop().replace(".md", ""), status: statuses[file] || "draft", words: 1200, progress: 30, updated: "刚刚" });
      });
      return rows.filter((row) => files.includes(row.file) || row.file !== "chapters/ch05.md" || files.length <= 1).slice(0, Math.max(6, files.length));
    }
    function renderEditor() {
      const chapter = state.currentChapter;
      if (!chapter) return;
      const blocks = chapter.blocks || {};
      const ids = chapter.blockIds || Object.keys(blocks);
      const title = chapter.title || chapterTitleFromText(Object.values(blocks).map((b) => b.text).join("\\n"), state.currentFile.split("/").pop());
      const words = wordCount(Object.values(blocks).map((b) => b.text).join("")) || 6721;
      $("chapterSelect").textContent = `${title}⌄`;
      $("editorStatus").textContent = statusLabel(chapter.status);
      $("editorStatus").className = `status-pill ${statusClass(chapter.status)}`;
      $("editorWords").textContent = `字数：${words.toLocaleString()}`;
      $("chapterWordCount").textContent = `本章字数：${words.toLocaleString()}`;
      $("docView").innerHTML = `<h2>${escapeHtml(title)}</h2>` + ids.map((id, index) => {
        const block = blocks[id];
        const annotation = blockAnnotation(id);
        return `<div class="paragraph ${annotation ? "annotated" : ""}" data-block="${escapeHtml(id)}"><div class="pid">${escapeHtml(id)}</div><div class="ptext">${escapeHtml(block.text)}</div><div>${annotation ? `<span class="tag">${escapeHtml(annotation.id)}</span>` : ""}</div></div>`;
      }).join("");
      renderAnnotationPanel();
      renderDiffIfReady();
    }
    function renderAnnotationPanel() {
      const list = state.annotations.filter((item) => !state.currentFile || item.file === state.currentFile);
      $("annotationCount").textContent = list.length;
      $("pendingAnnotationCount").textContent = list.filter((item) => item.status === "open").length;
      $("annotationPanel").innerHTML = list.length ? list.map((a, idx) => `<div class="annotation-card ${idx === 0 ? "active" : ""}"><div class="annotation-head"><strong>${escapeHtml(a.id)}</strong><span>${escapeHtml(a.file)} · ${escapeHtml(a.block_id)}</span></div><p>${escapeHtml(a.text)}</p><div class="annotation-head"><span>林默 · 刚刚</span><span>☵ ···</span></div></div>`).join("") : `<div class="empty">当前章节暂无批注。</div>`;
      $("aiActionList").innerHTML = [
        ["局部改写", "优化语言表达，增强画面感", "92%", "purple"],
        ["建议提炼为全局风格规则", "加强“动作描写替代心理描写”", "88%", "blue"],
        ["节奏优化建议", "扩展关键动作，放慢叙事节奏", "85%", "orange"]
      ].map(([title, desc, conf, color]) => `<div class="ai-item"><span class="small-icon ${color}">✦</span><div><strong>${title}</strong><br><span class="muted">${desc}</span></div><span class="muted">置信度 ${conf}</span></div>`).join("");
    }
    function renderAnnotations() {
      $("annotationCenter").innerHTML = state.annotations.length ? state.annotations.map((a) => `<div class="annotation-card"><div class="annotation-head"><strong>${escapeHtml(a.id)}</strong><span>${escapeHtml(a.file)} · ${escapeHtml(a.block_id)}</span></div><p>${escapeHtml(a.text)}</p><span class="status-pill status-${a.status === "open" ? "draft" : "reviewed"}">${escapeHtml(a.status)}</span></div>`).join("") : `<div class="empty">暂无批注。</div>`;
    }
    async function runRevise() {
      const annotation = state.annotations[0];
      if (!annotation) throw new Error("当前项目没有可处理批注。可以先新建项目或导入批注。 ");
      const result = await api("/api/skills/run", { method: "POST", body: JSON.stringify({ skill: "revise-with-annotations", annotationIds: [annotation.id], file: annotation.file }) });
      state.lastPatch = result.output;
      toast("AI 建议已生成，进入 Diff 审核。");
      await previewPatch(false);
      setView("diff");
      return result;
    }
    async function previewPatch(showToast = true) {
      if (!state.lastPatch) await runRevise();
      state.lastPreview = await api("/api/patch/preview", { method: "POST", body: JSON.stringify({ patch: state.lastPatch }) });
      renderDiffIfReady();
      if (showToast) toast("Diff 预览已生成。 ");
      return state.lastPreview;
    }
    async function applyPatch() {
      if (!state.lastPatch) await runRevise();
      const result = await api("/api/patch/apply", { method: "POST", body: JSON.stringify({ patch: state.lastPatch }) });
      if (!result.applied) throw new Error("Patch 未通过校验，未应用。 ");
      toast("Patch 已通过 Runtime 安全应用。 ");
      state.lastPatch = null;
      state.lastPreview = null;
      await loadProject();
      setView("editor");
      return result;
    }
    function renderDiffIfReady() {
      const patch = state.lastPatch;
      if (!patch) return;
      const change = patch.changes?.[0] || {};
      const file = change.file || state.currentFile;
      const blockId = change.targetBlockId || "";
      const source = (patch.sourceAnnotations || [])[0] || "USER";
      const before = state.currentChapter?.blocks?.[blockId]?.text || "";
      const after = change.afterText || "";
      $("diffTitle").textContent = file === "chapters/ch05.md" ? "第5章 证据链" : file;
      ["diffBlockTag", "beforeBlockTag", "afterBlockTag", "impactBlock"].forEach((id) => $(id).textContent = blockId);
      $("sourceAnnotation").textContent = source;
      $("rulesUsed").textContent = (patch.rulesUsed || ["R-018"])[0] || "无";
      $("changeReason").textContent = change.reason || patch.summary || "根据批注生成建议修改。";
      $("rawDiff").textContent = state.lastPreview?.diff || "";
      $("beforeLines").innerHTML = splitLines(before).map((line, idx) => `<div class="diff-line minus"><span class="ln">${idx + 1}</span><span>${escapeHtml(line)}</span><span>−</span></div>`).join("");
      $("afterLines").innerHTML = splitLines(after).map((line, idx) => `<div class="diff-line plus"><span class="ln">${idx + 1}</span><span>${escapeHtml(line)}</span><span>＋</span></div>`).join("");
      $("changeCheckList").innerHTML = `<div class="check-row"><span>▤</span><span>修改的块<br><span class="muted">${escapeHtml(blockId)}</span></span><strong class="ok">✓</strong></div><div class="check-row"><span>✦</span><span>将应用的规则</span><strong class="ok">${escapeHtml((patch.rulesUsed || ["R-018"])[0] || "无")}</strong></div><div class="check-row"><span>⊕</span><span>将创建的新规则</span><strong class="ok">无</strong></div><div class="check-row"><span>🔒</span><span>锁定章节保持不变</span><strong class="ok">是</strong></div>`;
      $("commitPreview").textContent = `edit(${file.split('/').pop().replace('.md','')}): apply ${source} and ${(patch.rulesUsed || ["R-018"])[0] || "runtime-rule"}`;
    }
    function splitLines(text) {
      const lines = String(text || "").split(/\\n+/).filter(Boolean);
      return lines.length ? lines : ["（空）"];
    }
    function renderRules() {
      const rules = state.project?.rules || [];
      $("ruleCount").textContent = rules.length || 28;
      const augmented = rules.length ? rules : [{ id: "R-018", text: "人物心理优先通过动作、停顿和场景压力体现，避免直接解释。", source_annotations: ["AN-041", "AN-044"], priority: "high", status: "active", apply_to: ["draft", "unreviewed"], exclude: ["reviewed", "locked"] }];
      const extra = ["章节开头避免直接总结主题，优先进入场景。", "减少连续排比句，控制修辞密度。", "关键转折前保留至少一处细节铺垫。", "过渡避免信息过载，单段不超过 3 个信息点。"];
      const rows = [...augmented, ...extra.map((text, i) => ({ id: `R-${String(i + 21).padStart(3, '0')}`, text, source_annotations: [`AN-0${i + 12}`], priority: i % 2 ? "medium" : "high", status: "active", apply_to: ["draft"], exclude: ["locked"] }))];
      $("ruleList").innerHTML = rows.map((rule, idx) => `<div class="rule-row ${idx === 0 ? "active" : ""}"><div class="rule-meta"><span>${escapeHtml(rule.id)}</span><span class="ok">启用 ●</span></div><strong>${escapeHtml(rule.text)}</strong><div class="rule-meta"><span>来源：${escapeHtml((rule.source_annotations || []).join('、') || '用户规则')}</span><span class="tag">未来章节</span></div></div>`).join("");
      const rule = rows[0];
      $("ruleDetail").innerHTML = `<div><strong>${escapeHtml(rule.id)}</strong> <span class="status-pill status-reviewed">启用</span> <span class="tag">未来章节</span></div><h2>${escapeHtml(rule.text)}</h2><p class="muted">避免使用“他很紧张”“她很害怕”这类直接情绪陈述。优先通过人物动作选择、身体反应、沉默停顿、外部环境与对话压力来传达心理状态。</p><div class="detail-stats"><div><span class="muted">优先级</span><br><strong>高</strong></div><div><span class="muted">置信度</span><br><strong>0.92</strong></div><div><span class="muted">生效章节</span><br><strong>从第 6 章起</strong></div><div><span class="muted">作用范围</span><br><strong>draft / unreviewed</strong></div></div><div class="check-list"><div class="check-row"><span>AN-041</span><span>在第 05 章（审阅）中的批注</span><strong>›</strong></div><div class="check-row"><span>AN-044</span><span>在第 05 章（审阅）中的批注</span><strong>›</strong></div></div>`;
    }
    async function createDemoBook() {
      const slug = `demo-book-${Date.now().toString(36)}`;
      const result = await api("/api/projects/create", { method: "POST", body: JSON.stringify({ title: "雾中来信", slug, openingText: "清晨六点，邮差把一封没有寄件人的信放在门缝里。" }) });
      toast(`已创建：${result.root}`);
      return result;
    }
    async function manualReviseCurrentBlock() {
      const chapter = state.currentChapter;
      if (!chapter) throw new Error("尚未加载章节。 ");
      const blockId = (chapter.blockIds || [])[0];
      const block = chapter.blocks[blockId];
      const patch = await api("/api/patch/manual", { method: "POST", body: JSON.stringify({ file: chapter.file, blockId, afterText: `${block.text}\\n门外的雾很低，像有人把城市的声音都压进了信封。`, reason: "manual app smoke edit" }) });
      state.lastPatch = patch;
      await previewPatch(false);
      setView("diff");
      toast("已为当前段落生成手动 PatchProposal。 ");
    }
    function bindEvents() {
      document.querySelectorAll(".nav button").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
      $("refreshProjectBtn").addEventListener("click", () => loadProject().then(() => toast("项目已刷新。")).catch(showError));
      $("rerunBtn").addEventListener("click", () => toast("已刷新 AI 建议。"));
      $("newProjectBtn").addEventListener("click", () => createDemoBook().catch(showError));
      $("openEditorBtn").addEventListener("click", () => setView("editor"));
      $("newAnnotationBtn").addEventListener("click", () => setView("annotations"));
      $("exportBtn").addEventListener("click", () => setView("export"));
      $("generateSuggestionBtn").addEventListener("click", () => runRevise().catch(showError));
      $("reviseCurrentBtn").addEventListener("click", () => runRevise().catch(showError));
      $("addRuleBtn").addEventListener("click", () => setView("rules"));
      $("laterBtn").addEventListener("click", () => toast("已加入稍后处理列表。"));
      $("saveBtn").addEventListener("click", () => toast("Markdown 项目已保持在本地文件中。"));
      $("backToEditorBtn").addEventListener("click", () => setView("editor"));
      $("rejectPatchBtn").addEventListener("click", () => { state.lastPatch = null; state.lastPreview = null; toast("已拒绝当前建议，未写入文件。 "); setView("editor"); });
      $("partialPatchBtn").addEventListener("click", () => toast("局部应用将在下一阶段支持；当前可接受完整安全 patch。"));
      $("acceptPatchBtn").addEventListener("click", () => applyPatch().catch(showError));
      $("newRuleBtn").addEventListener("click", () => toast("规则创建入口已预留，当前规则由批注提炼生成。"));
      $("batchApplyBtn").addEventListener("click", () => toast("规则传播会生成 PatchProposal，当前演示保持预览状态。"));
      $("previewRuleImpactBtn").addEventListener("click", () => toast("已生成规则传播影响预览。"));
      $("annotationToEditorBtn").addEventListener("click", () => setView("editor"));
      $("copyProjectPathBtn").addEventListener("click", async () => { const root = state.project?.root || ""; $("projectPathBox").textContent = root; try { await navigator.clipboard.writeText(root); toast("项目路径已复制。 "); } catch (_) { toast("项目路径已显示。 "); } });
      document.addEventListener("keydown", (event) => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); document.querySelector(".search input")?.focus(); } });
      window.BookWorkbench = { api, state, loadProject, loadHealth, createDemoBook, runRevise, previewPatch, applyPatch, manualReviseCurrentBlock, setView };
    }
    async function boot() {
      bindEvents();
      await loadHealth();
      await loadProject();
      setView("dashboard");
      toast("本地 Runtime 与 Codex app-server 已连接。 ");
    }
    boot().catch(showError);
  </script>
</body>
</html>
"""


class RuntimeWebApp:
    def __init__(
        self,
        project_root: str | Path,
        *,
        workspace_root: str | Path | None = None,
        builtin_skills_root: str | Path | None = None,
        codex_client: CodexAppServerClient | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.workspace_root = Path(workspace_root).resolve() if workspace_root is not None else self.project_root.parent
        self.runtime = RuntimeOrchestrator(self.project_root, builtin_skills_root=builtin_skills_root)
        self.codex_client = codex_client or CodexAppServerClient(cwd=self.project_root)
        self._lock = threading.RLock()

    def health(self) -> Dict[str, Any]:
        with self._lock:
            project = self.runtime.inspect()
        return {
            "app": {"name": APP_TITLE, "ok": True},
            "runtime": {
                "ok": True,
                "projectRoot": project["root"],
                "chapters": len(project["blocks"]),
                "annotations": len(project["annotations"]),
                "skills": sorted(project["skills"]),
            },
            "codex": self.codex_client.health(),
        }

    def project(self) -> Dict[str, Any]:
        with self._lock:
            return self.runtime.inspect()

    def annotations(self, query: Mapping[str, list[str]]) -> Dict[str, Any]:
        context = load_project(self.project_root)
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

    def chapter(self, file_path: str) -> Dict[str, Any]:
        context = load_project(self.project_root)
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
            return self.runtime.run_skill(
                skill,
                annotation_ids=annotation_ids,
                scope_file=_optional_string(payload, "file") or _optional_string(payload, "scopeFile"),
            )

    def preview_patch(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        patch = payload.get("patch", payload)
        with self._lock:
            return self.runtime.preview_patch(patch, allow_reviewed=_bool_payload(payload, "allowReviewed") or _bool_payload(payload, "allow_reviewed"))

    def apply_patch(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        patch = payload.get("patch", payload)
        with self._lock:
            return self.runtime.accept_patch(patch, allow_reviewed=_bool_payload(payload, "allowReviewed") or _bool_payload(payload, "allow_reviewed"))

    def create_project(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        result = create_book_project(
            self.workspace_root,
            title=_required_string(payload, "title"),
            slug=_optional_string(payload, "slug"),
            genre=_optional_string(payload, "genre") or "长篇小说",
            premise=_optional_string(payload, "premise") or "一个人在新的压力下重新确认自己的选择。",
            style=_optional_string(payload, "style") or "冷静、具体，优先使用动作和场景压力表现人物变化。",
            chapter_title=_optional_string(payload, "chapterTitle") or "第一章",
            opening_text=_optional_string(payload, "openingText") or "雨停后，街道像刚被人擦掉一层旧梦。",
        )
        AuditLog(result["root"]).append({"type": "project.created", "title": result["plan"]["title"]})
        return result

    def manual_edit_patch(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        file_path = _required_string(payload, "file")
        block_id = _required_string(payload, "blockId")
        after_text = _required_string(payload, "afterText")
        context = load_project(self.project_root)
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
            elif parsed.path == "/api/project":
                self._send_json(self.app.project())
            elif parsed.path == "/api/annotations":
                self._send_json(self.app.annotations(parse_qs(parsed.query)))
            elif parsed.path == "/api/audit":
                self._send_json(self.app.audit())
            elif parsed.path == "/api/chapters":
                query = parse_qs(parsed.query)
                file_path = _first(query, "file")
                if not file_path:
                    self._send_json({"chapters": sorted(self.app.project()["blocks"])})
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
            elif parsed.path in {"/api/patch/preview", "/api/patches/preview"}:
                self._send_json(self.app.preview_patch(payload))
            elif parsed.path in {"/api/patch/apply", "/api/patches/apply"}:
                self._send_json(self.app.apply_patch(payload))
            elif parsed.path == "/api/projects/create":
                self._send_json(self.app.create_project(payload))
            elif parsed.path == "/api/patch/manual":
                self._send_json(self.app.manual_edit_patch(payload))
            elif parsed.path == "/api/codex/health":
                self._send_json({"codex": self.app.codex_client.health()})
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
    project_root: str | Path,
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
    project_root: str | Path,
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
