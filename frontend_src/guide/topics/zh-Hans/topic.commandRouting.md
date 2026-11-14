---
{ "id": "topic.commandRouting", "title": "命令如何路由", "parent": "window.settingsChat", "order": 3, "keywords": ["本地", "混合", "意图", "句子匹配", "快捷方式", "快速路径", "TypeSafe", "JEV"] }
---
像“关掉厨房的灯”这样的简单命令不需要完整的 AI 模型。开启命令处理后，Phoenix 会先尝试更便宜的方式。

## Phoenix 尝试的顺序 {#order}

1. Home Assistant 的句子匹配，适用于所有已安装的 Assist 语言。它在你的家中运行，不产生费用。
2. 决策提供商（如果你使用）。它处理句子匹配遗漏的命令。
3. 你的 AI 模型，处理其他所有情况，与没有命令处理时一样。

一旦某一步执行了命令，后面的步骤就不会再使用。句子匹配或决策提供商更改了某些内容时，回复会说出执行的操作，让你知道发生了什么。

相关：[决策提供商](guide:window.settingsProviders#decision-provider)

## 何时适用 {#when}

你可以分别为 [Agent Chat](guide:window.settingsChat#chat-commands) 和[语音智能体](guide:window.settingsHome#voice-commands)开启命令处理。

它只适用于纯文本消息。Home Companion 密钥的消息始终直接交给 Home Companion。

## 后续消息 {#follow-ups}

对话中的第一条消息可以交给决策提供商。除 Cloudflare / Clef 外，所有决策提供商在语言受模型支持时也会接收后续消息。否则会交给你的 AI 模型。

## 决策提供商从不执行的操作 {#risky-actions}

决策提供商根据你的话选择操作。Phoenix 会检查你是否说出了设备名称，但无法检查操作本身。因此决策提供商从不执行以下操作：

- 解锁或打开门锁
- 打开或切换窗帘、车库门或阀门
- 打开或切换警报器
- 解除或触发警报系统

这些命令会改为交给你的 AI 模型，就像决策提供商没有把握时一样。访问密钥的权限和审批在那里依然适用。

## 限制 {#limits}

决策提供商对每个请求有时间限制，并可设置每小时限制。达到任一限制时，命令会交给你的 AI 模型，确保不会无人应答。

相关：[连接设置](guide:window.settingsProviders#decision-connection)

## 查看效果 {#effect}

路由统计会统计每条消息的处理方式，让你看到命令处理节省了什么。

相关：[阅读统计](guide:topic.routingStatistics)
