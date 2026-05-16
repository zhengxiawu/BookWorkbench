# 05. Skill 库设计

## 1. Skill 是什么

Skill 是产品中可复用的写作流程单元。它既可以被 App 内部调用，也可以通过 Codex app-server 作为 skill input item 调用。

每个 Skill 包含：

```text
SKILL.md         # 指令与边界
SKILL.json       # 可选：UI 元数据、权限、输入 schema
scripts/         # 可选：确定性脚本
references/      # 可选：说明、模板、样例
```

## 2. 第一批内置 Skills

### new-book-project

采访用户并创建项目。

输出：

- `book.spec.md`
- `outline.md`
- `style-guide.md`
- `rules.yaml`
- `chapters/ch01.md`
- `.bookai/project.yaml`
- `.bookai/chapter-status.yaml`

### revise-with-annotations

读取当前 scope 内的批注，根据批注与规则生成 PatchProposal。

### extract-writing-rules

从一组批注中提炼长期规则，生成 RuleProposal。

### propagate-rules

将已确认规则应用到 draft/unreviewed 章节，排除 reviewed/locked。

### import-annotations

把 Word/PDF/Markdown inline notes 导入 AnnotationIR。

### review-and-commit

审阅 patch 并生成 Git commit message。

## 3. Skill Scope

```text
builtin：App 自带，默认可信。
project：当前项目内，适合项目特定风格。
user：用户目录，适合个人长期工作流。
codex：Codex 自带或安装。
remote：未来插件市场。
```

## 4. Skill 调用策略

用户点击 AI 处理后：

```text
UI 选择 skill
  ↓
Runtime 构造上下文包
  ↓
Skill Manager 定位 SKILL.md
  ↓
CodexAppServerClient 发送 turn/start
  ↓
app-server 运行 skill
  ↓
输出 PatchProposal / RuleProposal / ProjectPlan
  ↓
Runtime 校验并展示
```

## 5. 每个写作 Skill 的硬性要求

1. 不直接写文件。
2. 必须输出结构化 proposal。
3. 必须说明来源批注。
4. 必须说明使用规则。
5. 必须说明排除范围。
6. 不得修改 locked 章节。
7. 修改 reviewed 章节必须标记为 requires_secondary_approval。
8. 如果没有足够上下文，必须请求用户补充。

## 6. 示例 Skill 输入

```json
{
  "project": "black-rain-after",
  "skill": "revise-with-annotations",
  "scope": {
    "type": "chapter",
    "chapterId": "ch05",
    "file": "chapters/ch05.md"
  },
  "annotations": ["AN-041", "AN-042"],
  "allowedOperations": ["replace_block", "insert_after_block"],
  "forbiddenTargets": ["status:locked", "status:reviewed_without_approval"]
}
```
