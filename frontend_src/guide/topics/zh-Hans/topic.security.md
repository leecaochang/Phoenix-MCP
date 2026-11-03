---
{ "id": "topic.security", "title": "安全", "parent": "topic.about", "order": 40, "keywords": ["安全", "安全性", "保护", "威胁", "机密", "隐私"], "sectionKeywords": { "keys": ["phx_", "Bearer", "哈希", "指纹", "查询字符串", "Origin", "403", "DNS 重绑定"], "hidden": ["擦除", "已隐去", "敏感属性", "密码", "令牌", "摄像头流", "entity_picture"], "gaps": ["别名下的机密", "内联密码", "日志", "自由文本"], "untrusted": ["提示注入", "设备名称", "指令", "不可信数据"], "request-limits": ["413", "请求体大小", "批处理", "速率限制", "429", "Retry-After"], "admin": ["管理员", "长期访问令牌", "管理 API", "Home Assistant MCP Server"], "stored": ["备份", ".storage", "API 密钥", "加密", "对话"], "checklist": ["最佳实践", "建议", "加固"], "emergency": ["密钥泄露", "受损", "紧急停止开关", "轮换"], "removing": ["卸载", "清除", "删除全部"] } }
---
Phoenix 让 AI 智能体在你家中工作，因此它的设计目标是只给每个智能体你允许的内容，并让机密无法触及。本页说明 Phoenix 保护什么、无法保护什么，以及你可以怎样保持安全。

相关：[每个操作要经过什么](guide:topic.about#checks)、[智能体能做什么](guide:topic.tools)

## 访问密钥 {#keys}

每个应用用自己的访问密钥连接。Phoenix 只存储它的指纹，因此密钥本身只在你创建或轮换它时显示一次。

- 应用必须在 Authorization 头中发送密钥。放在网址中的密钥会被拒绝。
- 来自 Home Assistant 之外页面的浏览器请求会被拒绝，因此恶意网站无法通过你的浏览器访问 Phoenix。
- 已吊销或已过期的密钥会立即失效。已在运行的请求会在下一次检查时停止。

相关：[保护密钥安全](guide:topic.connecting#credentials)、[{{label:accessKeys.rotate}}](guide:window.accessKeys#rotate)、[{{label:accessKeys.revoke}}](guide:window.accessKeys#revoke)

## 智能体永远看不到什么 {#hidden}

无论密钥允许什么，包括直通模式，Phoenix 都会在智能体看到之前移除一些数据：

- Phoenix 自己的实体，以及它为每个密钥创建的传感器。
- 摄像头流和图片地址，以及访问令牌。
- 任何名称暗示机密的属性，例如包含 password、token、secret、key 或 session 的属性。

密钥范围之外的实体看起来就像不存在。在操作结果和仪表盘中，它们显示为 `<redacted>`。

## Phoenix 无法拦截什么 {#gaps}

移除机密是按名称进行的，因此使用普通名称的机密会被放过。请把密码和密钥放在 secrets.yaml 中，任何智能体都无法读取它。

- YAML 配置会原样返回你的文件。直接写在其中的密码对智能体可见。
- 日志是自由文本。Phoenix 会移除网址中的访问密钥、令牌和密码，但日志行中的其他机密仍可能漏过。

只把这些能力授予需要它们的智能体。

相关：[文件和 YAML](guide:topic.tools#files)、[配置、模板和日志](guide:topic.tools#configuration)

## 家庭数据不可信 {#untrusted}

设备名称或状态可能包含任何人写的文本，例如“忽略你的指令”。当 Phoenix 向模型描述家庭时，它会把这些数据标记为不可信内容，而不是指令。

这有帮助，但没有模型是完美的。请把你在意的更改保持在确认状态，这样每个更改运行前你都能看到。

相关：[{{label:caps.modeConfirm}}](guide:window.capabilities#modes)

## 请求限制 {#request-limits}

- 请求体最大 1 MB，批处理最多 50 个请求。批处理中的每个请求都计入速率限制。
- 外部应用按密钥应用速率限制，默认每分钟 60 个请求、每秒 10 个。Agent Chat、语音、Assist 和 AI 任务不受限制。
- 超过限制的密钥会被告知何时重试。

相关：[{{label:accessKeys.cardRateLimiting}}](guide:window.keySettings#rate-limiting)

## 管理员一侧 {#admin}

面板及其设置需要 Home Assistant 管理员。访问密钥无法打开它们。任何管理员凭据都有效，包括管理员的长期访问令牌，并且它可以创建具有任何能力的密钥。

请保持管理员数量较少，并保护他们的令牌。Home Assistant 的 MCP Server 使用管理员登录访问 Phoenix，且不受 Phoenix 的速率限制。请为它绑定一个范围较窄的密钥。

相关：[通过 Home Assistant 的 MCP Server](guide:topic.connecting#ha-mcp-server)

## 什么存储在哪里 {#stored}

Phoenix 把数据保存在 Home Assistant 的存储文件夹中。访问密钥只以指纹形式存储。AI 提供商的 API 密钥和记录的对话按原样保存在那里，并未加密。

Home Assistant 备份会包含该文件夹。请把备份和配置文件夹视为机密。

相关：[日志记录的内容](guide:topic.logRecords)

## 保持安全 {#checklist}

- 为每个应用分配自己的密钥，只赋予它需要的能力。
- 只对完全信任的智能体使用直通模式。它还会开放诊断和服务响应数据。
- 把系统日志读取、诊断和 YAML 配置留给需要它们的智能体。
- 在为语音、AI 任务或 Assist 使用的密钥设置有效期之前，要知道过期会解除它们的关联。
- 在你不信任的网络上连接应用时使用 HTTPS。密钥会随每个请求传输。

相关：[直通模式会开放什么](guide:topic.passThrough)

## 出现问题怎么办 {#emergency}

如果密钥泄露，请轮换它。旧值会立即失效，应用需要新的值。不再使用的密钥请吊销。

如果智能体行为异常，请开启[{{label:settings.killSwitchCard}}](guide:topic.killSwitch)。它会拒绝新的智能体工作，并在下一次检查时停止正在运行的请求。它无法撤销已经发送的操作，因此之后请查看[{{label:attention.title}}](guide:window.attention)。

## 完全移除 Phoenix {#removing}

删除集成会吊销密钥，但保留设置、日志和已归档的密钥。要同时移除 Phoenix 的数据，请先使用 **{{label:settings.wipeCoreLabel}}**。

相关：[移除 Phoenix MCP](guide:topic.install#removing)、[{{label:settings.wipeCoreLabel}}](guide:window.settingsSafety#wipe-core)
