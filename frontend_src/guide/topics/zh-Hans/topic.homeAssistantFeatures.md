---
{ "id": "topic.homeAssistantFeatures", "title": "语音智能体、AI 任务或 Assist 工具提供商", "parent": "window.settingsHome", "order": 2, "keywords": ["对话智能体", "LLM API", "控制 Home Assistant", "哪个模型"] }
---
Phoenix 可以通过三种方式与 Home Assistant 的 AI 功能协作。区别在于由谁的模型来回答，以及访问密钥控制什么。

## 语音智能体 {#voice-agent}

Phoenix 使用你的账户和模型，自己回答 Home Assistant 对话。它能做什么受其访问密钥以及说话者权限的限制。

如果你希望语音和 Assist 背后使用 Phoenix 自己的模型和工具，请使用它。

回复在 8,000 个字符处停止，并说明已被截断。Assist 显示相同的文本。

## AI 任务 {#ai-task}

Phoenix 使用你的账户和模型，为自动化和脚本运行 Home Assistant 的 AI 任务操作。它在自己的访问密钥范围内代表整个家庭行动。如果 Home Assistant 告知任务由谁发起，任务只拥有该用户的权限。

该用户需要对 AI 任务实体有控制权限，即使任务未指定实体。不是由人发起的自动化不需要。

它还可以为自动化和脚本回答 Home Assistant 的“评估”操作，请参阅[决策](guide:window.settingsHome#ai-decisions)。

## Assist 工具提供商 {#assist-tool-provider}

由另一个集成的模型回答，Phoenix 只借用工具。绑定的访问密钥决定该模型获得哪些工具，发起请求的 Home Assistant 用户会进一步限制它们。Phoenix 在这里不运行模型，也不使用你的额度。

如果你已有喜欢的对话智能体，并希望它通过 Phoenix 的控制访问你的家，请使用它。

从 Home Assistant 2026.11 起，一次对话会保留开始时的工具。修改后的备注和权限会随下一次工具调用送达模型，已失去的工具会被立即拒绝。新增的工具需要开始新的对话。

相关：[{{label:workspace.homeAssistant}}](guide:window.settingsHome)
