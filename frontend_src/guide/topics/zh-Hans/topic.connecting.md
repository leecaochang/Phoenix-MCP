---
{"id": "topic.connecting", "title": "连接外部应用", "parent": "topic.about", "order": 20, "keywords": ["MCP 客户端", "连接", "设置", "外部智能体"], "sectionKeywords": {"commands": ["Claude Code", "Gemini CLI", "Antigravity", "Codex", "Cursor", "DeepSeek Harness", "代码片段", "命令"], "credentials": ["机密", "泄露", "保护", "Bearer"], "several-keys": ["多个配置", "多个密钥", "服务器名称"], "connection": ["URL", "端点", "Streamable HTTP", "SSE", "协议", "会话"], "reconnect": ["工具已更改", "刷新工具"], "primer": ["指令", "备注", "技能", "SKILL.md"], "ha-mcp-server": ["Home Assistant MCP Server", "/api/mcp", "托管", "连接器"], "problems": ["401", "403", "429", "503", "未授权", "禁止", "排查"], "remote": ["远程访问", "Nabu Casa", "外出", "VPN", "Tailscale", "反向代理", "https"]}}
---
外部应用，例如编码助手或桌面聊天应用，使用一个访问密钥连接到 Phoenix。应用自己的模型负责思考，Phoenix 决定它可以做什么。

## 获取设置命令 {#commands}

Phoenix 会显示 Claude Code、Gemini CLI、Antigravity CLI、Codex、Cursor 和 DeepSeek Harness 的确切命令或配置，并为其他 MCP 应用提供一个通用版本。它们出现在三个位置：

- [{{label:accessKeys.setUpAgent}}](guide:wizard.connect) 的连接步骤。
- **{{label:accessKeys.postCreateConnectTitle}}**，就在你创建密钥之后。
- **{{label:accessKeys.helpConnect}}**，在你轮换密钥之后。

命令中包含密钥本身，Phoenix 只显示一次。要为现有密钥再次查看这些命令，请轮换该密钥并更新应用。

每个应用的选项卡都会链接到该应用自己的设置指南。之后请重启或重新加载应用。

## 保护密钥安全 {#credentials}

访问密钥就像密码，可用于其权限允许的一切。Phoenix 只存储它的指纹，因此无法再次显示它。

不要把它放在共享文件和聊天记录中。Claude Code、Gemini 和 Codex 的设置命令会把它放在命令行上，如果别人能读取你的 shell 历史记录，请清除它。如果泄露了，请[轮换](guide:window.accessKeys#rotate)密钥：旧值会立即失效。

## 使用多个密钥 {#several-keys}

为每个应用或任务分配自己的密钥，只赋予它需要的权限。每个密钥在应用中都有自己的服务器名称，例如 phx-home，因此一个应用可以同时持有多个密钥。

重命名密钥不会影响它继续工作，但新的设置命令会使用新名称。

## 连接 {#connection}

应用连接到你的 Home Assistant 地址加 /api/phoenix-mcp，并把密钥作为 Bearer 令牌发送。设置命令中的 URL 是你的浏览器使用的地址；如果应用通过其他方式访问 Home Assistant，请在那里修改。

Phoenix 通过 Streamable HTTP 使用 MCP。使用 SSE 传输的旧应用设置无法连接。耗时较长的操作会每 15 秒发送一条消息来保持连接。

## 从家庭网络外连接 {#remote}

Phoenix 没有自己的远程服务。家庭网络外的应用会像浏览器一样访问它：通过你的 Nabu Casa 地址、你自己的域名，或 Tailscale 等 VPN。把设置命令中的 URL 改为该地址，后面加上 /api/phoenix-mcp。

在家庭网络外请使用 https 地址，因为密钥会随每个请求发送。反向代理必须传递 Authorization 标头，并允许长时间响应。托管应用从其提供商的服务器连接，因此家庭网络或 VPN 地址对它们无效。

## 工具发生变化时 {#reconnect}

有些应用只在连接时读取工具列表。在你更改密钥的能力或权限后，请重新连接应用以查看新列表。Phoenix 也会在智能体下次调用时告知它：工具已更改时重新连接；如果只是需要审批的操作变了，则告知其工具没有变化。

相关：[重新连接智能体](guide:window.accessKeys#reconnect)

## 智能体会收到什么 {#primer}

应用连接时，Phoenix 会向它发送简短的安全工作说明：审批和试运行如何运作、哪些操作需要你的审批，以及密钥的备注。Home Companion 密钥会收到一个更短的固定版本。

支持技能的应用还可以安装 Phoenix MCP 技能，这是一个较长的指南，显示在 **{{label:wizard.skillSectionTitle}}** 下。它对所有人都是相同的，不包含关于你家的任何信息。

## 通过 Home Assistant 的 MCP Server {#ha-mcp-server}

登录 Home Assistant 本身的应用，例如托管连接器，可以通过 Home Assistant 的 Model Context Protocol 集成访问 Phoenix 的工具，地址为 /api/mcp/phoenix_mcp。在 2026.11 之前，它叫 MCP Server。它们使用绑定到 [Assist 工具提供商](guide:topic.homeAssistantFeatures#assist-tool-provider)的密钥，并且需要管理员登录。

在这条路径上，密钥的速率限制不适用，审计日志会把调用显示为 Assist，应用只有主动请求时才会收到 Phoenix 的说明。

从 2026.11 起，请检查该集成的选项。除非你另行选择，新条目会提供所有 API，包括 Phoenix 的 API。只读选项只给应用留下标记为只读的工具，但它不能替代密钥的能力设置。

在这条路径上，Home Assistant 其他 API 的工具不经过 Phoenix，因此密钥、审批、MESA 和审计日志都不覆盖它们。当两个 API 提供同名工具时，Home Assistant 会添加前缀，例如 `phoenix-mcp__`。

## 无法连接时 {#problems}

- **未授权 (401)**：密钥错误、已吊销或已过期，或者被放在 URL 中而不是 Authorization 头中。
- **禁止 (403)**：某个网页试图从其他站点连接。桌面应用和命令行应用不受影响。
- **请求过多 (429)**：密钥达到速率限制，或同时打开的连接过多。应用应等待后重试。
- **不可用 (503)**：紧急停止开关已开启，或 Phoenix 仍在启动。
