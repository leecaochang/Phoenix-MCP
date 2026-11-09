---
{ "id": "window.settingsChat", "title": "{{label:settings.agentcliCard}}", "parent": "workspace.settings", "order": 2, "keywords": ["聊天"], "sectionKeywords": { "chat-global": ["Shift+A", "快捷键", "键盘"], "chat-memory": ["历史", "记忆", "上下文"], "chat-steps": ["步骤", "工具调用"], "chat-style": ["语气", "风格"] } }
---
Agent Chat（Phoenix 自己的聊天窗口）的设置。请在聊天窗口本身选择访问密钥、账户和模型。

## {{label:routing.enableChat}} {#chat-commands}

在不使用你的 AI 模型的情况下执行简单命令，例如开灯。Phoenix 使用 Home Assistant 自己的句子匹配，适用于所有已安装的 Assist 语言。默认关闭。

如果正在使用决策提供商，它会处理句子匹配遗漏的命令。其他所有内容照常交给你的 AI 模型。

这不适用于 Home Companion 密钥，也不适用于非纯文本消息。

相关：[决策提供商](guide:window.settingsProviders#decision-provider)、[命令如何路由](guide:topic.commandRouting)

## {{label:settings.agentcliGlobalLabel}} {#chat-global}

让 Agent Chat 可以在任何 Home Assistant 页面打开，而不只是此面板。按 Shift+A 显示或隐藏它。默认开启。

Shift+A 需要你的 Home Assistant 个人资料中开启键盘快捷键。在此面板中，即使该设置关闭，Shift+A 仍然有效。

更改会在页面下次完整加载时生效。**{{label:settings.killSwitchCard}}** 会在所有地方关闭 Agent Chat。

## {{label:settings.agentcliMemoryLabel}} {#chat-memory}

Phoenix 随每条新消息发送的较早对话量，以字符计：0 到 960,000（默认 200,000）。你的新消息也计入其中。

工具结果同样计入，而且通常占比最大。较早的轮次会整体丢弃，从最早的开始。设为 0 时不发送较早的轮次。

基本拉丁字母以外的字符（例如中文）每个按六个计算，因为 Phoenix 按这种方式衡量请求是否超出大小上限。

更多记忆会给模型更多上下文，但每条消息的费用也更高。上下文窗口较小的模型（例如在本地运行的模型）需要更低的值。

Related: [长消息](guide:window.agentChat#memory-warning)

## {{label:settings.agentcliStepsLabel}} {#chat-steps}

模型针对一条消息可以执行的最大工具步骤数：3 到 100（默认 20）。修复格式错误工具调用所用的步骤也计入。

达到限制时，Agent Chat 会询问是否继续。语音智能体和 AI 任务使用相同的限制，但会停止。

## {{label:settings.conversationStyle}} {#chat-style}

回复的语气：{{label:settings.styleDirect}}（默认）、{{label:settings.styleWarm}}、{{label:settings.styleCalmGuide}}、{{label:settings.styleLively}} 或 {{label:settings.styleTechnical}}。这只改变措辞，不改变 Phoenix 的行为。

## {{label:settings.detailLevel}} {#chat-detail}

回复说多少内容：{{label:settings.detailConcise}}（默认）、{{label:settings.detailBalanced}} 或 {{label:settings.detailDetailed}}。

## {{label:settings.homeFocused}} {#chat-home-focused}

要求模型只谈论你的家，并拒绝其他话题。默认关闭。

这是偏好，不是安全控制：模型是被要求，而不是被强制。它的访问密钥决定它实际能做什么。

相关：[访问密钥](guide:workspace.accessKeys)

## {{label:routingStats.title}} {#chat-statistics}

**{{label:routingStats.show}}** 会打开一个窗口，统计 Agent Chat 的消息如何处理：由句子匹配、决策提供商还是你的 AI 模型。

相关：[阅读统计](guide:topic.routingStatistics)
