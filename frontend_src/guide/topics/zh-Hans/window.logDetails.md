---
{ "id": "window.logDetails", "title": "{{label:workspace.logDetails}}", "parent": "workspace.logs", "order": 3, "keywords": ["条目", "记录", "负载", "转录"], "sectionKeywords": { "step": ["上一个", "下一个"], "audit-entry": ["负载", "请求 ID", "参数"], "conversation": ["轮次", "复制", "原始数据", "技术细节", "工具结果"] } }
---
你在[{{label:shell.tabAudit}}](guide:window.auditLog)或[{{label:conversations.title}}](guide:window.conversationLog)中选择的记录，完整显示。

## 在记录间切换 {#step}

**{{label:workspace.previousRecord}}** 和 **{{label:workspace.nextRecord}}** 会按你选择记录时日志显示的顺序移动。之后到达的条目不会包含在内，直到你再次选择记录。

## 审计条目 {#audit-entry}

条目的每个字段，包括其 **{{label:audit.rowRequestId}}**。工具调用还会显示其 **{{label:audit.rowPayload}}**：工具的名称和参数，其中密码、令牌、密钥以及警报或门锁代码会被隐藏。过长的负载会被截断。

如果访问密钥此后被重命名，它的旧名称会显示在当前名称后的括号中。

## 对话 {#conversation}

标题会概括对话：来自哪里、何时，以及包含多少轮次、工具调用和更改。其后是各个轮次，最早的在前，每次 25 个。

每个轮次显示询问的内容、每次工具调用及其结果、带更改前后内容的更改，以及回复。长文本会被折叠。**{{label:conversations.copy}}** 会复制一段文本。**{{label:conversations.details}}** 和 **{{label:conversations.technical}}** 会显示精确记录的内容。
