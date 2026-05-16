# 03. Codex App-Server 接入设计

## 1. 为什么直接基于 app-server 开发

App 不需要“Open in Codex”作为主路径。更好的方式是把 Codex app-server 作为内置 agent host：

```text
用户在 Manuscript App 中操作
  ↓
App 启动/连接 codex app-server
  ↓
App 将 skill、上下文、权限、scope 传给 app-server
  ↓
Codex 执行 agent/skill 任务
  ↓
App 接收 stream events 与 approval requests
  ↓
Runtime 校验 patch 并写入 Git
```

## 2. 传输方式

MVP 推荐使用 `stdio`：

```bash
codex app-server
```

App 通过子进程 stdin/stdout 通信。

WebSocket 可以作为调试或未来远程模式，但必须绑定 localhost 并启用 token auth。

## 3. 初始化流程

```text
1. App 启动。
2. 检查 Codex CLI 是否存在。
3. spawn codex app-server。
4. 发送 initialize。
5. 创建 thread，cwd 指向当前 manuscript project。
6. 调用 skills/list 读取可用 skills。
7. 将 App 内置 skills 作为额外 skill scope 注入。
8. 用户点击 AI 处理时发送 turn/start。
```

## 4. Turn 输入结构

建议 turn/start 输入包含：

```json
{
  "threadId": "thread_001",
  "input": [
    {
      "type": "text",
      "text": "处理当前章节 ch05 的作者批注，输出 PatchProposal，不要直接修改文件。"
    },
    {
      "type": "skill",
      "name": "revise-with-annotations",
      "path": "/app/skills/revise-with-annotations/SKILL.md"
    }
  ]
}
```

## 5. App 内置 skills 与 Codex skills 的关系

App 启动时加载：

```text
/app/skills                      # App 内置
~/.manuscript/skills             # 用户自定义
project/.codex/skills           # 项目级
Codex skills/list                # Codex 已安装
```

如果重名，优先级建议：

```text
project > user > app builtin > codex > remote
```

但安全 skill 不允许被覆盖，例如：

- `safe-patch-apply`
- `chapter-lock-policy`
- `git-checkpoint-policy`

## 6. Approval 拦截

当 app-server 产生 command/fileChange approval request 时：

```text
app-server request
  ↓
CodexAppServerClient
  ↓
Safety Policy Engine
  ↓
如果是文稿修改：要求 PatchProposal
  ↓
UI 展示审批
  ↓
用户确认
  ↓
respond approval
```

默认策略：

- command execution：按风险显示审批。
- file change：涉及 Markdown/chapter/rules/status 时必须二次确认。
- locked chapter：自动拒绝。
- 不含来源批注或用户明确指令的修改：自动降级为“仅建议”。

## 7. app-server 事件映射

原始 Codex 事件不直接进入 UI，统一映射：

```text
item/started → run.step.started
item/messageDelta → agent.message.delta
item/commandExecution/outputDelta → command.output.delta
item/fileChange/requestApproval → approval.required.fileChange
item/tool/call → tool.call.requested
item/completed → run.step.completed
error → run.failed
```

## 8. Dynamic Tools

第一版可以不启用 dynamic tools，只要求 Codex 输出结构化 JSON。第二版再开放工具：

- `manuscript.read_annotations`
- `manuscript.get_chapter_status`
- `manuscript.propose_patch`
- `manuscript.preview_patch`
- `manuscript.apply_patch`
- `manuscript.create_rule`

注意：dynamic tools 当前应被视为高级/实验接口，必须有兼容测试。

## 9. 兼容策略

每次锁定 Codex 版本后运行：

```bash
codex app-server generate-ts --out ./vendor/codex-schemas
codex app-server generate-json-schema --out ./vendor/codex-schemas-json
```

将生成 schema 放入测试，保证 adapter 能处理当前版本消息。
