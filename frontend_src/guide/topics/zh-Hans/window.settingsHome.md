---
{ "id": "window.settingsHome", "title": "{{label:workspace.homeAssistant}}", "parent": "workspace.settings", "order": 3, "keywords": ["语音", "Assist", "助手"], "sectionKeywords": { "ai-decisions": ["评估", "ai_task.evaluate"], "voice-agent": ["对话智能体", "管道"], "voice-assistant": ["管道"], "ai-task": ["生成数据"], "assist-tool-provider": ["LLM API", "控制 Home Assistant"] } }
---
把 Phoenix 连接到 Home Assistant 自己的 AI 功能。每个选项卡设置一个功能，每个功能中都有一个访问密钥决定 Phoenix 可以做什么。

- {{label:settings.voiceCard}}：Phoenix 使用你的账户和模型回答 Home Assistant 的语音和 Assist 对话。
- {{label:settings.aiTaskCard}}：Phoenix 使用你的账户和模型运行 Home Assistant 的 AI 任务操作。
- {{label:settings.assistCard}}：另一个集成的模型使用 Phoenix 的工具。Phoenix 不运行自己的模型。

相关：[使用哪一个](guide:topic.homeAssistantFeatures)

## {{label:settings.voiceCard}} {#voice-agent}

让 Phoenix 成为一个名为 Phoenix MCP 的 Home Assistant 对话智能体。请先设置访问密钥、账户和模型：三者都设置好后，智能体才会出现。

### {{label:settings.voiceEnableLabel}} {#voice-enable}

开启智能体。默认关闭。关闭它还会移除 Phoenix 创建的 Assist 助手。

**{{label:settings.killSwitchCard}}** 开启时，智能体仍会保留，但只会回复它不可用。

### {{label:settings.voiceAssistSetup}} {#voice-assistant}

创建一个名为 Phoenix MCP 的 Home Assistant 助手，使用此智能体，并采用 Home Assistant 默认的语音转文字和文字转语音。智能体完全设置好后可用。

关闭它会删除助手。Home Assistant 始终保留至少一个助手，因此如果它是唯一的助手，它会保留，但 Phoenix 会停止管理它。

如果你清除访问密钥、账户或模型，或吊销密钥，Phoenix 也会移除其助手。当它是唯一助手时，Home Assistant 会保留它，Phoenix 会告知你。添加另一个助手后，请在 Home Assistant 中移除它。

### {{label:settings.voicePreferredLabel}} {#voice-preferred}

让 Phoenix 的助手成为 Home Assistant 默认使用的助手。关闭它会把这个角色交给 Home Assistant 列表中的第一个助手，它可能不是你之前使用的那个。

### {{label:settings.accessKey}} {#voice-access-key}

决定智能体可以做什么的访问密钥。你只能选择有效的密钥。创建或吊销密钥后，列表会随之更新。如果密钥被吊销或过期，它会在此处被清除。

Home Companion 密钥会把对话交给 Home Companion 处理，并隐藏 **{{label:routing.enableVoice}}**。

### {{label:settings.providerAccount}} {#voice-provider}

其模型负责回答的账户。决策提供商账户不会列出。请先在[{{label:workspace.aiProviders}}](guide:window.settingsProviders)中添加账户。

### {{label:settings.model}} {#voice-model}

负责回答的模型。请先选择账户。如果提供商停用该模型，Phoenix 会切换到其后续模型。

### {{label:routing.enableVoice}} {#voice-commands}

像在 Agent Chat 中一样，在不使用你的 AI 模型的情况下执行简单命令。默认关闭。

相关：[命令如何路由](guide:topic.commandRouting)

### {{label:settings.conversationStyle}} {#voice-style}

回复的语气，选项与 [Agent Chat](guide:window.settingsChat#chat-style) 相同。默认：{{label:settings.styleDirect}}。

### {{label:settings.detailLevel}} {#voice-detail}

回复说多少内容。默认：{{label:settings.detailConcise}}。

### {{label:settings.homeFocused}} {#voice-home-focused}

要求模型只谈论你的家。这是偏好，不是安全控制。

### {{label:routingStats.title}} {#voice-statistics}

**{{label:routingStats.show}}** 会打开一个窗口，统计语音命令的处理方式。

相关：[阅读统计](guide:topic.routingStatistics)

## {{label:settings.aiTaskCard}} {#ai-task}

为 Home Assistant 提供一个名为 Phoenix MCP AI Task 的 AI 任务实体，自动化和脚本用它生成文本或数据。它在自己的访问密钥范围内代表整个家庭行动。如果 Home Assistant 告知任务由谁发起，任务只拥有该用户的权限。

### {{label:settings.aiTaskEnableLabel}} {#ai-task-enable}

开启实体。默认关闭。只有当访问密钥、账户和模型都已设置时，实体才会存在。

如果你清除了其中任何一项，Phoenix 会移除实体，你在 Home Assistant 中对它所做的任何更改都会丢失。

### {{label:settings.aiTaskDefaultLabel}} {#ai-task-default}

让 Phoenix 的实体成为 Home Assistant 的默认 AI 任务。这会立即替换任何当前默认项，无需询问。快速帮助会显示它替换了哪一个。

关闭它会让 Home Assistant 没有默认项。之前的默认项不会恢复。

### {{label:settings.accessKey}} {#ai-task-access-key}

决定 AI 任务可以做什么的访问密钥。它的工作方式与[语音智能体的](guide:#voice-access-key)相同。

### {{label:settings.providerAccount}} {#ai-task-provider}

其模型运行任务的账户。

### {{label:settings.model}} {#ai-task-model}

运行任务的模型。

### {{label:settings.conversationStyle}} {#ai-task-style}

文本结果的语气。默认：{{label:settings.styleDirect}}。

### {{label:settings.detailLevel}} {#ai-task-detail}

文本结果说多少内容。默认：{{label:settings.detailBalanced}}。请求结构化数据的任务则使用它们自己的格式。

## {{label:settings.aiDecisionsHeading}} {#ai-decisions}

为 Home Assistant 添加第二个实体 Phoenix MCP Decisions，用于“评估”操作。自动化和脚本可以用它对传入的文本或数据提出是非题、选择题和评分题。决策提供商负责回答，每个回答都附带概率。

此部分仅在 Home Assistant 2026.11 或更高版本上、且正在使用决策提供商时显示。要添加或选择决策提供商，请前往 [{{label:workspace.aiProviders}}](guide:window.settingsProviders#decision-provider)。

问题和传入的数据会发送到你的决策提供商，每次发送的请求都计入[请求限额](guide:window.settingsProviders#decision-request-limit)。根据回答执行操作的自动化以 Home Assistant 的身份运行，不受 Phoenix 检查。

发起“评估”操作的人需要对 Decisions 实体有控制权限，即使操作未指定实体。不是由人发起的自动化不需要。每次调用和拒绝都会写入[审计日志](guide:topic.logRecords#audit-entries)。

### {{label:settings.aiDecisionsEnableLabel}} {#ai-decisions-enable}

打开此实体。默认关闭。只有在此开关打开且已选择访问密钥时，实体才存在。

撤销访问密钥或移除决策提供商会关闭此功能并移除实体，你在 Home Assistant 中对它所做的更改也会丢失。

### {{label:settings.aiDecisionsDefaultLabel}} {#ai-decisions-default}

将其设为 Home Assistant 中未指定实体的“评估”操作的默认实体，并立即替换当前默认实体。打开{{label:settings.aiDecisionsEnableLabel}}并选择访问密钥后可用。

### {{label:settings.accessKey}} {#ai-decisions-access-key}

允许“评估”操作运行的访问密钥。它与 AI 任务的密钥相互独立。它必须处于有效状态，并将 **{{label:caps.cap_service_response.label}}** 设为 {{label:caps.modeAllow}}；直通密钥已视为此设置。

实体权限和 MESA 在此不适用：此操作没有目标实体，也不会改变任何内容。

### {{label:routingStats.aiTaskTitle}} {#ai-decisions-statistics}

**{{label:routingStats.aiTaskShow}}** 会打开一个窗口，统计该实体回答、拒绝或失败的“评估”操作，以及它们使用的决策 token。请参阅[评估](guide:topic.routingStatistics#evaluate)。

## {{label:settings.assistCard}} {#assist-tool-provider}

把 Phoenix 的工具借给在别处运行的模型，例如设为使用 Phoenix MCP（限定范围）控制 Home Assistant 的对话智能体。Phoenix 在这里不运行自己的模型。

### {{label:settings.assistBoundAccessKey}} {#assist-access-key}

另一个模型获得其工具的访问密钥。使用 **{{label:settings.assistNotBound}}**（默认）时，不提供任何工具。**{{label:settings.killSwitchCard}}** 开启时也是如此。

更改密钥会拒绝之前轮次的工具调用，因此对话无法保留旧密钥的权限。Home Assistant 的 MCP Server 使用同一个密钥，且不受密钥的速率限制。

相关：[通过 Home Assistant 的 MCP Server](guide:topic.connecting#ha-mcp-server)
