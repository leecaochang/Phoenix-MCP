---
{ "id": "window.keySettings", "title": "{{label:workspace.keySettings}}", "parent": "workspace.accessKeys", "order": 6, "sectionKeywords": { "guidance": ["备注", "指令", "提示词"], "announce-all": ["工具列表", "排查"], "inline-wait": ["审批", "超时", "待处理"], "assist-exposure": ["公开", "直通模式"], "oauth-sign-in": ["OAuth", "ChatGPT", "Claude", "连接器", "登录", "手机应用"], "oauth-allow-new": ["倒计时", "登录窗口期", "10 分钟", "停止"], "oauth-sign-ins": ["已登录的应用", "退出登录", "移除应用"], "rate-limiting": ["限流", "每分钟请求数"] } }
---
所选密钥的更多设置，分布在三个标签页上。**{{label:accessKeys.cardGuidance}}** 保存给其智能体的备注。**{{label:accessKeys.cardAgentBehavior}}** 设置其工具如何提供、请求等待审批多久，以及速率限制。**{{label:accessKeys.tabOAuth}}** 让应用用此密钥登录。

## {{label:accessKeys.cardGuidance}} {#guidance}

Phoenix 会把这些备注添加到使用此密钥的每个智能体的指令中，例如房间和设备的名称，或谁使用该智能体。最多 2,000 个字符。选择**保存**以保留它们，或选择 **{{label:accessKeys.notesDiscard}}**。

指引只是建议。它绝不授予访问权限，智能体可能忽略它。智能体及其模型提供商会收到它，因此绝不要在这里填写密码或其他机密。

当你更改它时，已连接的智能体会在下一次工具结果中收到新指引。Phoenix 会隐藏密钥无法看到的实体 ID，并移除看起来像机密的文本。

## {{label:accessKeys.cardAgentBehavior}} {#agent-behavior}

### {{label:accessKeys.announceAllLabel}} {#announce-all}

向智能体提供所有 Phoenix 工具，即使此密钥无法使用它们。对这些工具的调用仍会被拒绝。默认关闭；仅用于排查问题，因为它会让模型花费更多并导致调用失败。

### {{label:accessKeys.inlineWaitLabel}} {#inline-wait}

需要审批的请求在应用收到“待处理”答复之前，等待你决定的时间：关闭（默认），或 30 到 180 秒。关闭时，应用会立即得知请求正在审批中等待。

这仅适用于外部应用。Agent Chat 最多等待你的决定 15 分钟。语音智能体、Assist 和 AI 任务会报告更改正在等待审批，然后继续。如果密钥已有请求在等待，新请求不会等待。

### {{label:accessKeys.assistExposureLabel}} {#assist-exposure}

把直通密钥限制为 Home Assistant 中向 Assist 公开的实体。默认关闭。它只对直通密钥显示；限定范围的密钥改用权限树。

## {{label:accessKeys.tabOAuth}} {#oauth-sign-in}

让只接受服务器地址的应用（例如 ChatGPT、Claude 及其手机应用）使用此密钥，而无需你把密钥粘贴到任何地方。默认关闭。发送密钥的智能体无论如何都会继续工作。

当你开启 **{{label:oauth.switchLabel}}** 时，Phoenix 会显示 **{{label:oauth.address}}**。在应用中用该地址添加一个自定义连接器，客户端 ID 留空。应用会打开 Home Assistant，你在那里批准此次登录。

应用需要能从互联网通过 HTTPS 访问 Home Assistant。如果 Home Assistant 没有 HTTPS 地址，Phoenix 会说明这一点，并且不会开启登录。如果你通过未加密的 HTTP 打开了 Phoenix，开关将不可用，Phoenix 会给出指向其 HTTPS 地址的链接。请在那里打开 Phoenix，再设置和批准登录。

关闭它会让通过 OAuth 使用此密钥的每个应用退出登录。轮换或吊销密钥也一样。紧急停止开关会暂停已登录的应用，直到你解除它。

相关：[通过 OAuth 登录的应用](guide:topic.connecting#oauth)

### {{label:oauth.allowNew}} {#oauth-allow-new}

应用只能在你允许后的 10 分钟内发起登录。开启开关会开始第一个 10 分钟。倒计时会显示剩余时间。

批准一次登录会结束登录窗口期，Phoenix 会显示哪个应用已登录。选择 **{{label:oauth.stopWindow}}** 可提前结束它。在连接另一个应用之前，或当应用要求你重新登录时，请选择 **{{label:oauth.allowNew}}**。在没有开放登录窗口期时到达的登录链接不会起作用。

Phoenix 在每个地方显示的倒计时都相同，包括连接说明和审批对话框。

### {{label:oauth.signInsTitle}} {#oauth-sign-ins}

为此密钥批准的应用，以及每个应用的返回地址和最后使用时间。已登录的应用会在密钥有效期内一直保持登录。选择 **{{label:oauth.remove}}**，然后选择 **{{label:common.confirm}}**，可让你不再使用的应用退出登录；点击其他任何地方会取消。一个密钥最多可有 10 个已登录的应用。

在应用自己那边删除它或断开连接，并不总会通知 Phoenix。通知了 Phoenix 的应用会从此列表中消失；否则它会一直留在这里，直到你移除它。最后使用时间能看出哪些应用已经停止使用。

## {{label:accessKeys.cardRateLimiting}} {#rate-limiting}

速率限制位于 **{{label:accessKeys.cardAgentBehavior}}** 标签页。它们仅适用于外部应用。Agent Chat、语音智能体、Assist 和 AI 任务从不受限制。每个字段在你离开时保存。

### {{label:accessKeys.rateRequestsLabel}} {#rate-requests}

密钥在任意一分钟内可以发出的最大请求数。设为 0 可关闭速率限制。新密钥从 60 开始。

### {{label:accessKeys.rateBurstLabel}} {#rate-burst}

任意一秒内的最大请求数。设为 0 表示不限制每秒请求数。新密钥从 10 开始。每分钟请求数为 0 时显示为灰色。应用打开或关闭连接时发送的消息不计入此限制，只计入每分钟限制。
