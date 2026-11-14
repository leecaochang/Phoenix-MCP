---
{ "id": "window.settingsProviders", "title": "{{label:workspace.aiProviders}}", "parent": "workspace.settings", "order": 1, "keywords": ["API 密钥", "模型", "LLM", "账户", "额度", "TypeSafe", "JEV", "Clef", "Gemini Decision"], "sectionKeywords": { "provider-add": ["新账户", "Anthropic", "Claude", "DeepSeek", "OpenAI", "ChatGPT", "GPT", "Gemini", "Google", "Grok", "xAI", "Groq", "Kimi", "Moonshot", "Meta", "Llama", "MiniMax", "Mistral AI", "NVIDIA NIM", "Xiaomi MiMo", "Tencent Cloud TokenHub", "OpenCode", "Ollama", "OpenRouter", "Infron", "Together AI", "Cerebras", "Cloudflare Workers AI", "Workers AI", "Fireworks AI", "Alibaba Cloud Model Studio", "Qwen", "DashScope", "Z.ai", "Zhipu", "GLM", "深度求索", "通义千问", "千问", "阿里云百炼", "百炼", "智谱", "月之暗面", "腾讯云", "小米", "谷歌", "离线", "本地模型", "价格", "计费"], "decision-threshold": ["threshold", "confidence", "probability", "0.90"], "decision-provider": ["TypeSafe", "JEV", "快速路径", "OpenCode", "System One", "Gemini Decision", "GPT Luna Decision", "Cloudflare", "Workers AI", "Clef", "Clef Flash", "账户 ID", "谷歌", "OpenAI Decisions", "gpt-6-luna"], "tool-loading": ["load_tools", "延迟加载", "工具搜索", "token", "工具太多", "上下文"] } }
---
添加 Phoenix 使用的 AI 账户。每个账户需要 API 密钥，或者对于本地模型，需要服务器地址。

Agent Chat、语音智能体和 AI 任务各使用其中一个账户，并搭配你选择的模型。它们会把对话和工具结果发送给该提供商。

相关：[哪些数据会离开你的家](guide:topic.security#leaves-home)

## 账户 {#provider-accounts}

列出每个账户及其提供商和默认模型。选择一个账户以显示其选项，再次选择则隐藏它们。

如果账户有问题，例如提供商不再提供某个模型，其选项中会显示警告。

### {{label:settings.accountChangeModel}} {#change-model}

从提供商提供的模型中选择。无法使用工具的模型会灰显，因为 Phoenix 需要工具来执行操作。

**{{label:settings.agentcliModelAuto}}** 使用适合代理工作的当前模型，跳过预览版和已宣布停用的模型。如果你自己选择模型，Phoenix 会一直保留它，直到提供商停用它。

之后会切换到提供商指定的后续模型。没有的话，切换到同系列中最新的模型，再没有就切换到同一档次中最新的模型，例如另一个 Haiku 模型。

如果都没有，Phoenix 会保留已停用的模型并显示警告，请另选一个模型。名称以 `:free` 结尾的模型只会切换到另一个免费模型。在本地 Ollama 服务器上，Phoenix 只在同一系列内切换，**{{label:settings.agentcliModelAuto}}** 也从不选择云端模型。

当 **{{label:settings.agentcliVerifyOptions}}** 开启时，保存时还会检查模型支持哪些选项。这会消耗少量 API 额度。

### {{label:settings.accountRefreshModels}} {#refresh-models}

重新加载提供商的模型列表。这不消耗 token。在本地 Ollama 服务器上，Phoenix 会逐个询问每个模型。

### {{label:settings.accountCheckOptions}} {#check-options}

通过发送几个非常简短的请求，查明模型支持哪些努力级别以及是否能读取图像。

这会消耗少量额度，因此 Phoenix 会先询问。将指针悬停在按钮上可查看上次运行的时间。

### 移除账户 {#remove-account}

移除账户会删除其存储的密钥。你需要先就地确认。

如果语音智能体或 AI 任务使用了该账户，它们会失去它，Phoenix 会移除为它们创建的 Assist 助手和 AI 任务实体。

相关：[语音智能体](guide:window.settingsHome#voice-agent)、[AI 任务](guide:window.settingsHome#ai-task)

### {{label:settings.toolLoadingLabel}} {#tool-loading}

决定 Agent Chat、语音智能体和 AI 任务如何把访问密钥的工具交给此账户的模型。

**{{label:settings.toolLoadingAuto}}**（默认）只发送一小组常用工具，其余工具按组列出。请求需要时，模型会加载更多工具。访问密钥的工具很多（约 75 个或以上）时，Phoenix 才这样做；工具较少时，会全部发送。

分批加载让每个请求发送的 token 少得多，结果相同。请求需要尚未加载的工具时，会多一步。语音智能体一开始就带有设备控制工具，因此语音命令不用等这一步。

**{{label:settings.toolLoadingAlways}}** 无论工具多少都分批加载。**{{label:settings.toolLoadingNever}}** 总是发送全部工具，但只接受 128 个工具的提供商除外。

工具加载不会给模型更多权限，Phoenix 照常检查每次调用。Home Companion 密钥不受 **{{label:settings.toolLoadingLabel}}** 设置影响。

## 添加账户 {#provider-add}

1. 选择工具栏上的 **{{label:settings.addAccount}}** 以显示提供商。
2. 选择一个提供商并填写其字段。选择 **{{label:settings.showSecret}}** 可检查你粘贴的 API 密钥。
3. 选择 **{{label:settings.agentcliValidate}}**。这会向提供商核实详细信息，但暂时不保存任何内容。
4. 选择一个模型，或保留 **{{label:settings.agentcliModelAuto}}**，然后选择 **{{label:settings.agentcliDone}}**。

如果验证后更改了字段，请再次验证。Phoenix 会在保存账户前再检查一次详细信息。

### 每个提供商要求什么 {#provider-kinds}

大多数提供商只需要 API 密钥。有些还需要知道你的账户所在位置：

- OpenAI、Grok、Kimi、Mistral AI 和 MiniMax：密钥签发所在的区域。
- Xiaomi MiMo：按量付费，或你的 Token Plan 区域。
- Tencent Cloud TokenHub：签发密钥的站点。密钥只能在该站点使用。
- OpenCode：Zen 或 Console 路径。你只能在其中一条路径上添加密钥。
- Z.ai：Standard API、Coding Plan 或 China。同一个密钥可以为每个方案各保存一次。
- Qwen：除了 API 密钥，还需要你的工作区基础 URL，即一个公开的 HTTPS 地址。
- Cloudflare Workers AI：你的 Cloudflare 账户 ID，以及具有 Workers AI 权限的 API 令牌。Phoenix 只列出标记为可使用工具的模型。免费方案每天只有少量额度，部分模型需要 Workers Paid。
- Ollama（本地）：你的 Ollama 服务器地址。Home Assistant 会连接它，因此请使用 Home Assistant 主机可以访问的地址。普通 `http://` 流量不加密；请参阅[哪些数据会离开你的家](guide:topic.security#leaves-home)。

有些方案只供编程工具使用：Z.ai 的 Coding Plan、Xiaomi MiMo 的 Token Plan，以及阿里云的 Coding Plan 和 Token Plan。它们的条款禁止在 Home Assistant 这类应用中使用，提供商可能会限流或封禁密钥。选择这些方案时 Phoenix 会给出警告。语音、AI Task 和 Agent Chat 请使用提供商的按量付费 API。

Groq、Mistral AI 和 Z.ai 每个请求最多接受 128 个工具，Phoenix 对 Gemini、Kimi、OpenRouter、Infron 和 Cloudflare Workers AI 也采用同样的上限。访问密钥的工具更多时，无论[工具加载](guide:#tool-loading)如何设置，Phoenix 都会为它们分批加载工具。

## {{label:settings.catalogChecks}} {#catalog-checks}

Phoenix 会不时重新读取每个账户的模型列表。这能让列表保持最新，使 **{{label:settings.agentcliModelAuto}}** 可以切换到更新的模型。

### {{label:settings.providerChecksLabel}} {#provider-checks}

每小时一次，Phoenix 会重新读取早于以下时段的模型列表。这不消耗 token。默认开启。

关闭时，Phoenix 仍会在需要时读取列表：新账户、提供商报告缺少模型，以及你切换到 **{{label:settings.agentcliModelAuto}}** 时。

### {{label:settings.providerChecksHoursLabel}} {#provider-checks-hours}

Phoenix 会按计划重新检查提供商的模型列表：6 到 720 小时（默认 24）。

## {{label:routing.providerSection}} {#decision-provider}

决策提供商是一个小型快速模型，负责执行简单命令，这样你的主 AI 模型就不必处理。它节省完整模型轮次的费用和等待时间。

它与 **{{label:routing.enableChat}}** 和 **{{label:routing.enableVoice}}** 配合使用。Phoenix 会先尝试 Home Assistant 自己的句子匹配，决策提供商处理句子匹配遗漏的命令。

从以下决策提供商中选择：

- TypeSafe / JEV：TypeSafe 的 JEV 模型，使用 TypeSafe API 密钥。
- OpenCode / System One：JEV、Gemini Decision 或 GPT Luna Decision，使用 OpenCode Console API 密钥。
- Google / Gemini Decision：Google 的 Gemini Decision 模型，使用 Gemini API 密钥。
- Cloudflare / Clef：Workers AI 上的 Clef 或 Clef Flash，使用你的 Cloudflare 账户 ID 和 API 令牌。
- OpenAI / Decisions：OpenAI 的 gpt-6-luna 决策模型，使用 OpenAI API 密钥。

添加一个账户并选择其模型。同一时间只能使用一个决策提供商。每个账户的名称旁会显示其模型。

决策账户与同一家公司的任何 AI 提供商账户相互独立。如果你已经在 AI 提供商中使用该密钥，也必须在此再次添加。

除 Cloudflare / Clef 外，所有决策提供商也会处理依赖对话内容的后续消息。Clef 会把这类消息交给你的 AI 提供商。参见[后续消息](guide:topic.commandRouting#follow-ups)。

jev-preview 模型只观察，从不执行命令。

要使用它，请选择其账户。选择 **{{label:routing.noProvider}}** 可停止使用它；命令随后只依赖句子匹配。

它还可以为自动化和脚本回答 Home Assistant 的“评估”操作，请参阅[决策](guide:window.settingsHome#ai-decisions)。

相关：[命令如何路由](guide:topic.commandRouting)

### {{label:routing.decisionThreshold}} {#decision-threshold}

决策账户执行命令前必须达到的确定程度，范围 0.70 到 1.00（默认 0.90）。选择账户即可显示此设置。每个账户各自保存。

阈值越低，决策提供商自己处理的命令越多，转给你的 AI 提供商的命令越少。这可能会让更多错误通过，因为每个模型各不相同。

## {{label:routing.connectionSettings}} {#decision-connection}

这些限制在使用决策提供商时适用。

### {{label:routing.decisionBudget}} {#decision-timeout}

Phoenix 等待决策提供商的时间，从 250 到 30,000 毫秒（默认 5,000）。如果提示超时，请求会照常交给你的主 AI 模型。

### {{label:routing.requestLimit}} {#decision-request-limit}

Phoenix 每小时可以发送的最大决策请求数。设为 0（默认）表示不限制。每次使用决策提供商都会增加计数，包括失败的请求。

达到限制后，命令会交给你的主 AI 模型，直到该小时结束。
