---
{ "id": "topic.logRecords", "title": "日志记录的内容", "parent": "workspace.logs", "order": 10, "keywords": ["审计", "历史", "保留", "隐私", "隐去"], "sectionKeywords": { "audit-entries": ["工具调用", "管理", "审批", "MESA"], "not-audited": ["缺失", "401", "未授权"], "outcomes": ["已允许", "已拒绝访问", "速率受限", "待审批", "无效请求", "未找到", "未实现", "已取消", "结果未知"], "audit-keeping": ["保留", "存储", "磁盘", "清除"], "conversation-sources": ["Agent Chat", "语音", "AI 任务", "Assist", "MCP 客户端", "Home Companion"], "conversation-contents": ["工具结果", "提供商", "模型", "机密", "代码"], "conversation-keeping": ["保留", "存储", "限制"], "linked": ["请求 ID", "审批 ID"] } }
---
Phoenix 保留两种日志。审计日志是每个操作及其结果的简短记录。对话日志在开启后会完整保留智能体说过和做过的事情。

## 什么构成审计条目 {#audit-entries}

- **智能体操作**：来自 MCP 客户端、Agent Chat、语音智能体、Assist、AI 任务或 Home Companion 的每次工具调用。条目包含工具名称和参数，绝不包含其结果。
- **其他 MCP 请求**：列出工具、开始和结束会话，以及密钥无权发出的请求。如果应用在更改期间断开连接，名为 `request/cancelled` 的条目会记录这一情况。
- **评估操作**：每次 `ai_task/evaluate` 调用和拒绝，记在 Decisions 访问密钥名下。条目包含拒绝原因，绝不包含问题或数据。
- **MESA 阻止**：MESA 每拒绝一个实体就增加一条条目。这些条目需要 mesa_core.audit 记录器处于默认级别 INFO；在 Home Assistant 的日志记录器设置中提高级别会隐藏它们，但阻止仍会发生。
- **审批**：每个进入审批队列的请求，以及它的后续结果：已批准、已执行、已拒绝、已取消或失败。
- **你在面板中的更改**：访问密钥、权限、设置、MESA 策略、还原、清除等管理员操作，显示为管理员而非访问密钥。设置更改会说明改了什么，即使关闭日志记录。

## 什么不被记录 {#not-audited}

没有有效访问密钥的请求、Phoenix 启动期间或紧急停止开关阻止的请求，以及不调用任何工具的聊天或语音轮次。大多数面板读取也不被记录。

开启[对话日志](guide:window.settingsLogs#conversation-log)以查看对话本身。

## 结果 {#outcomes}

- **{{label:audit.outcomeAllowed}}**：已执行。
- **{{label:audit.outcomeDenied}}**：被权限或 MESA 拒绝，或被拒绝、过期、取消的审批。
- **{{label:audit.outcomePendingApproval}}**：等待你的审批。
- **{{label:audit.outcomeRateLimited}}**：密钥发送了过多请求。
- **{{label:audit.outcomeInvalidRequest}}**、**{{label:audit.outcomeNotFound}}** 和 **{{label:audit.outcomeNotImplemented}}**：请求格式错误、指向不存在的对象，或要求 Phoenix 不支持的操作。Phoenix 无法确认的更改也记为{{label:audit.outcomeInvalidRequest}}，尽管它可能已经生效。
- **{{label:audit.outcomeCancelled}}** 和 **{{label:audit.outcomeUnknown}}**：请求完成之前，应用已离开或失去权限。{{label:audit.outcomeCancelled}}表示没有任何操作到达你的家。{{label:audit.outcomeUnknown}}表示更改可能已运行，请在 Home Assistant 中检查。

记录哪些结果由[审计日志设置](guide:window.settingsLogs#audit-log)决定。

## 审计条目保留多久 {#audit-keeping}

审计日志默认保留最新的 10,000 条条目，并每 15 分钟以及 Home Assistant 停止时保存到磁盘。可在[{{label:workspace.logsNotifications}}](guide:window.settingsLogs#log-max-entries)中更改这两项。

面板中没有任何操作会自行清除审计日志。Phoenix 核心数据清除会清除它。移除 Phoenix 会保留它。

## 对话日志记录什么 {#conversation-sources}

- **Agent Chat、语音智能体和 AI 任务**：每个轮次，包含你的话、回复以及每次工具调用。
- **MCP 客户端、Assist 和 Home Companion**：每次工具调用及其结果。Phoenix 看不到用户输入的内容，因此没有提示词。

轮次内完成的工作，例如之后执行的已批准操作，会加入该轮次。同一聊天中的 Agent Chat 轮次会归为一组。

## 对话包含什么 {#conversation-contents}

每次工具调用及其完整结果、带更改前后内容的更改、审批、错误，以及请求经过的路由。来自 Agent Chat、语音和 AI 任务的条目还会注明 AI 提供商和模型。

密码、令牌、密钥以及警报或门锁代码在保存前会被隐藏。图像和文件只做记录，不会存储。Phoenix 给 AI 的指令不会被记录，token 用量则进入路由统计。

## 对话保留多久 {#conversation-keeping}

默认保留最新的 100 个对话，最长 7 天、25 MB。在[{{label:workspace.logsNotifications}}](guide:window.settingsLogs#conversation-log)中更改限制。过大的条目会保留其结果，但丢弃部分细节。

使用对话日志窗口中的 **{{label:conversations.clear}}** 清除日志。核心数据清除也会清除它。

## 在两种日志中找到同一个请求 {#linked}

工具调用的请求 ID 在两种日志中相同，审批的 ID 也相同。在审计日志中，按 **{{label:audit.rowRequestId}}** 或 **{{label:audit.rowResource}}** 筛选；在对话日志中，搜索它。
