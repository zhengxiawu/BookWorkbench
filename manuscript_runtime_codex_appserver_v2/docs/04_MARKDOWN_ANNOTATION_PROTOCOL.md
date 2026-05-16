# 04. Markdown 注释与 Block Anchor 协议

## 1. 设计原则

Markdown 是产品内部主写作格式，但 Markdown 原生没有评论线程。因此采用：

```text
Markdown 正文：只保存 block anchor
.bookai/annotations.jsonl：保存完整批注
.bookai/block-index.json：保存 block 索引
```

这样可以保持正文干净、Git diff 清楚，也能支持复杂批注状态和线程。

## 2. Block Anchor

每个可批注段落前插入：

```md
<!-- mw:block id=ch05-p018 hash=sha256:a91f3c -->
我坐在审讯室里，盯着对面的男人。他沉默，眼神里没有任何波动。
```

字段说明：

- `mw:block`：Manuscript Workbench block anchor。
- `id`：稳定段落 ID。
- `hash`：段落文本 hash，用于检测锚点漂移。

## 3. Sidecar Annotation

`.bookai/annotations.jsonl`：

```json
{
  "id": "AN-041",
  "file": "chapters/ch05.md",
  "target": {
    "blockId": "ch05-p018",
    "selectedText": "我坐在审讯室里，盯着对面的男人。他沉默，眼神里没有任何波动。",
    "prefix": "雨停后，城市像被人用灰布覆盖了头。",
    "suffix": "“你最后一次见到她是什么时候？”我问。",
    "startOffset": 0,
    "endOffset": 35,
    "beforeHash": "sha256:a91f3c"
  },
  "body": {
    "text": "这里太像 AI 了，不要解释内心，要用动作来表现。",
    "type": "style",
    "priority": "high"
  },
  "metadata": {
    "author": "林默",
    "createdAt": "2026-05-15T10:30:00-04:00",
    "status": "open"
  }
}
```

## 4. 选中一段创建批注的流程

```text
用户选中文字
  ↓
UI 获取 selection range
  ↓
MarkdownDocumentService 映射到 blockId
  ↓
如果无 blockId，自动插入 anchor
  ↓
AnnotationEngine 创建 AnnotationIR
  ↓
写入 annotations.jsonl
  ↓
UI 右侧显示批注卡片
```

## 5. 批注重新定位

如果正文已修改，批注定位策略：

1. 找 `blockId`。
2. 比对 `beforeHash`。
3. hash 一致：直接用 offset。
4. hash 不一致：找 selectedText exact match。
5. 失败：用 prefix/suffix fuzzy match。
6. 仍失败：标记 `needs_remap`。

## 6. Inline Note 兼容模式

为了兼容 VS Code/Obsidian/Typora 等外部编辑器，支持 inline note：

```md
<!-- mw:note
id: AN-041
target: ch05-p018
type: style
priority: high
-->
这里太像 AI 了，不要解释内心，要用动作来表现。
<!-- /mw:note -->
```

导入时将 inline note 转为 sidecar；导出时可选择保留或移除。

## 7. 批注状态

```text
open：待处理
in_review：正在生成修改建议
patched：已有 patch
accepted：patch 已接受
rejected：patch 被拒绝
converted_to_rule：已转为规则
resolved：已解决
ignored：忽略
needs_remap：锚点丢失，需要人工确认
```
