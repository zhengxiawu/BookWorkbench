# 02. 产品模块设计与模块交互

## 1. 总体模块

```text
Manuscript App UI
  ↓
Runtime Orchestrator
  ↓
├── Codex AppServer Client
├── Skill Manager
├── Markdown Document Service
├── Annotation Engine
├── Rule Engine
├── Patch Engine
├── Git Service
├── Safety Policy Engine
├── Import/Export Adapters
└── Event Bus
```

## 2. UI 层

### 责任

- 渲染项目、章节、文稿、批注、规则、diff、运行事件。
- 捕获用户选区并创建注释。
- 发起 Skill Run。
- 展示 Codex app-server 的 stream events。
- 处理用户审批：确认文件写入、确认 patch、确认 Git commit。

### 不负责

- 不直接写文件。
- 不直接调用模型改文稿。
- 不直接修改 `.bookai` 元数据。

## 3. Runtime Orchestrator

### 责任

Runtime 是核心控制器。

它负责：

- 管理项目状态。
- 管理 task/run 生命周期。
- 调度 Skill Manager。
- 调用 Codex app-server。
- 调用 Patch Engine 校验 AI 输出。
- 调用 Git Service 生成 checkpoint/commit。
- 向 UI 发出事件。

### 关键接口

```ts
type StartRunInput = {
  projectId: string;
  skillName: string;
  scope: RunScope;
  userInstruction?: string;
};

interface RuntimeOrchestrator {
  startRun(input: StartRunInput): Promise<RunRef>;
  cancelRun(runId: string): Promise<void>;
  approveAction(runId: string, approvalId: string, decision: Decision): Promise<void>;
  acceptPatch(patchId: string, selection?: PatchSelection): Promise<CommitRef | ApplyResult>;
}
```

## 4. Codex AppServer Client

### 责任

- 启动 `codex app-server`。
- 发送 initialize。
- 创建 thread。
- 启动 turn。
- 传入 skill input item。
- 监听 stream events。
- 处理 command/fileChange approval request。
- 将 app-server 原始事件映射为 Runtime 事件。

### 设计原则

不要让 UI 或 Runtime 深处直接依赖 app-server 原始 schema。所有 Codex 细节都封装在 `CodexAppServerClient`。

```ts
interface CodexAppServerClient {
  start(): Promise<void>;
  initialize(): Promise<void>;
  startThread(input: ThreadInput): Promise<ThreadRef>;
  startTurn(input: TurnInput): Promise<TurnRef>;
  respondToApproval(requestId: string, decision: ApprovalDecision): Promise<void>;
  onEvent(cb: (event: CodexBridgeEvent) => void): Unsubscribe;
}
```

## 5. Skill Manager

### 责任

- 加载 App 内置 Skill 库。
- 加载用户自定义 Skill。
- 加载项目 `.agents/skills`。
- 读取 Codex 可用 skills list。
- 管理 scope：builtin / project / user / codex / remote。
- 生成给 app-server 的 skill input item。

### Skill Scope

```text
builtin：App 自带，最安全，版本随 App 发布。
project：项目目录内，适合某本书专用流程。
user：用户目录内，适合个人长期偏好。
codex：Codex 自带或安装的 skills。
remote：未来云端 marketplace。
```

## 6. Markdown Document Service

### 责任

- 读取 Markdown。
- 解析 AST。
- 自动补全 block id。
- 构建 block index。
- 将 UI 选区映射到 block。
- 提供上下文片段给 Skill。
- 应用通过校验的 patch。

### 核心数据

```ts
type MarkdownBlock = {
  id: string;
  file: string;
  startOffset: number;
  endOffset: number;
  text: string;
  hash: string;
};
```

## 7. Annotation Engine

### 责任

- 创建 Markdown 注释。
- 读取 `.bookai/annotations.jsonl`。
- 导入 Word/PDF 注释。
- 统一成 AnnotationIR。
- 解析批注状态：open / resolved / ignored / converted_to_rule。
- 做 anchor remapping。

## 8. Rule Engine

### 责任

- 读取/写入 `rules.yaml`。
- 管理规则版本。
- 计算当前章节适用规则。
- 根据批注提议新规则。
- 控制规则传播范围。

## 9. Patch Engine

### 责任

- 接收模型/Skill 输出的 PatchProposal。
- 校验 patch 是否合法。
- 生成 diff view。
- 支持局部接受。
- 应用 patch 到 Markdown。
- 生成 commit message。

## 10. Safety Policy Engine

### 责任

执行安全规则：

- AI 不直接写入文稿。
- locked 章节不可改。
- reviewed 章节需二次确认。
- 所有文件变更必须先生成 patch。
- 所有 patch 必须引用来源批注或用户明确指令。
- 规则传播只能触达 draft/unreviewed。
- 所有落地修改生成 Git checkpoint。

## 11. Git Service

### 责任

- 初始化 Git。
- 检查工作区状态。
- 创建 checkpoint。
- 提交 accepted patch。
- 回滚。
- 显示修改历史。

## 12. Import/Export Adapters

### MarkdownAdapter

内部主格式。

### DocxAdapter

读取 `.docx` comments、track changes，并转为 AnnotationIR 或 RevisionIR。

### PdfAdapter

读取 PDF annotations，恢复页码、rect/quadpoints、批注内容，并尝试映射到 Markdown block。

### WpsAdapter

不作为唯一解析器。它只负责：

- 嵌入 WPS WebOffice 预览/编辑。
- 接收保存回调。
- 保存后仍交给 DocxAdapter/PdfAdapter 解析真实文件。

## 13. Event Bus

统一事件：

```ts
type AppEvent =
  | { type: "run.started"; runId: string }
  | { type: "skill.started"; skillName: string }
  | { type: "agent.message"; text: string }
  | { type: "approval.required"; approval: ApprovalRequest }
  | { type: "patch.ready"; patchId: string }
  | { type: "run.completed"; summary: string }
  | { type: "run.failed"; error: string };
```
