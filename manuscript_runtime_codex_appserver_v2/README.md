# Manuscript Runtime + Codex App-Server 方案 B 设计包 v2

本设计包将产品定位为：**以 Markdown 阅读/批注为主界面，以 Manuscript Runtime 管理流程与安全边界，以 Codex app-server 执行 agent/skill 任务的长文写作工作台。**

## 目录

- `docs/01_PRODUCT_INTERACTION_DESIGN.md`：产品交互设计。
- `docs/02_MODULE_ARCHITECTURE.md`：模块划分与模块间交互。
- `docs/03_CODEX_APPSERVER_INTEGRATION.md`：Codex app-server 接入方式。
- `docs/04_MARKDOWN_ANNOTATION_PROTOCOL.md`：Markdown 注释与 block anchor 协议。
- `docs/05_SKILL_LIBRARY_DESIGN.md`：内置 Skill 库与 scope 设计。
- `docs/06_SAFETY_RUNTIME.md`：安全策略、审批、patch、Git 边界。
- `screenshots/`：GPT Image 生成的产品页面与整体设计稿。
- `skills/`：示例 Skill 目录。
- `schemas/`：AnnotationIR、PatchProposal、SkillEvent JSON Schema。
- `sample_project/`：示例 Markdown 项目结构。

## 当前结论

1. App 负责 UI、Runtime、流程、状态、规则、安全、Git。
2. Codex app-server 负责 agent 会话、stream event、skills 调用、approval request。
3. App 启动时加载本地 Skill 库，同时可读取 Codex 用户/仓库 scope 中的 skills。
4. Markdown 是主写作格式；批注默认存 sidecar，Markdown 正文只保留 block anchor。
5. AI 不能直接写入文稿；必须输出 PatchProposal，经 Runtime 校验、用户审阅、Git commit 后落地。
