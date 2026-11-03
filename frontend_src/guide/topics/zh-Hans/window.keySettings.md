---
{ "id": "window.keySettings", "title": "{{label:workspace.keySettings}}", "parent": "workspace.accessKeys", "order": 6, "sectionKeywords": { "guidance": ["备注", "指令", "提示词"], "announce-all": ["工具列表", "排查"], "inline-wait": ["审批", "超时", "待处理"], "assist-exposure": ["公开", "直通模式"], "rate-limiting": ["限流", "每分钟请求数"] } }
---
所选密钥的更多设置：给其智能体的指引、工具如何提供、请求等待审批的时间，以及速率限制。

## {{label:accessKeys.cardGuidance}} {#guidance}

Phoenix 会把这些备注添加到使用此密钥的每个智能体的指令中，例如房间和设备的名称，或谁使用该智能体。最多 2,000 个字符。选择**保存**以保留它们，或选择 **{{label:accessKeys.notesDiscard}}**。

指引只是建议。它绝不授予访问权限，智能体可能忽略它。智能体及其模型提供商会收到它，因此绝不要在这里填写密码或其他机密。

当你更改它时，已连接的智能体会在下一次工具结果中收到新指引。Phoenix 会隐藏密钥无法看到的实体 ID，并移除看起来像机密的文本。

## {{label:accessKeys.cardAgentBehavior}} {#agent-behavior}

### {{label:accessKeys.announceAllLabel}} {#announce-all}

向智能体提供所有 Phoenix 工具，即使此密钥无法使用它们。对这些工具的调用仍会被拒绝。默认关闭；仅用于排查问题，因为它会让模型花费更多并导致调用失败。

### {{label:accessKeys.inlineWaitLabel}} {#inline-wait}

需要审批的请求在应用收到“待处理”答复之前，等待你决定的时间：关闭（默认），或 30 到 180 秒。关闭时，应用会立即得知请求正在审批中等待。

这仅适用于外部应用。Agent Chat、语音智能体、Assist 和 AI 任务始终直接排队。如果密钥已有请求在等待，新请求不会等待。

### {{label:accessKeys.assistExposureLabel}} {#assist-exposure}

把直通密钥限制为 Home Assistant 中向 Assist 公开的实体。默认关闭。它只对直通密钥显示；限定范围的密钥改用权限树。

## {{label:accessKeys.cardRateLimiting}} {#rate-limiting}

速率限制仅适用于外部应用。Agent Chat、语音智能体、Assist 和 AI 任务从不受限制。每个字段在你离开时保存。

### {{label:accessKeys.rateRequestsLabel}} {#rate-requests}

密钥在任意一分钟内可以发出的最大请求数。设为 0 可关闭速率限制。新密钥从 60 开始。

### {{label:accessKeys.rateBurstLabel}} {#rate-burst}

任意一秒内的最大请求数。设为 0 表示不限制每秒请求数。新密钥从 10 开始。每分钟请求数为 0 时显示为灰色。
