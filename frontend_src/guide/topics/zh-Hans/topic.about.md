---
{ "id": "topic.about", "title": "关于 Phoenix MCP", "order": 1, "keywords": ["介绍", "概览", "是什么", "入门", "MCP"], "sectionKeywords": { "ways": ["Agent Chat", "外部应用", "Claude", "ChatGPT", "客户端"], "checks": ["能力", "权限", "MESA", "审批", "层"], "token": ["长期访问令牌", "LLAT", "令牌"], "start": ["向导", "设置智能体", "第一步"] } }
---
Phoenix MCP 让 AI 智能体安全地在你的 Home Assistant 中工作。每个智能体都有自己的访问密钥，由你决定该密钥能查看和执行什么。

## 两种使用方式 {#ways}

- **Agent Chat**：直接在 Home Assistant 内与 AI 模型聊天。你添加 AI 提供商的账户，Phoenix 替你运行智能体。
- **外部应用**：用访问密钥连接 MCP 客户端，例如桌面助手或编码工具。应用自己的模型负责思考。
- **Home Assistant 自带的助手**：Phoenix 可以作为语音智能体回答、运行 AI 任务操作，或把自己的工具借给其他助手。

无论哪种方式，每个操作都会经过相同的检查，并记录在审计日志中。

相关：[{{label:agentchat.title}}](guide:window.agentChat)、[连接外部应用](guide:topic.connecting)、[语音智能体、AI 任务或 Assist 工具提供商](guide:topic.homeAssistantFeatures)

## 每个操作要经过什么 {#checks}

1. **能力**：密钥整体上可以做哪类事情，例如控制设备或编辑自动化。每项能力可设为拒绝访问、允许或确认。
2. **权限**：密钥可以查看和更改哪些区域、设备和实体。
3. **MESA**：无论密钥允许什么，对每个设备要多谨慎：让智能体操作、先询问你，还是不让它碰。
4. **审批**：设为确认的操作会等待你批准。

相关：[智能体能做什么](guide:topic.tools)、[安全](guide:topic.security)、[运行 Phoenix](guide:topic.running)、[管理 API](guide:topic.adminApi)、[{{label:accessKeys.cardCapabilities}}](guide:window.capabilities)、[权限如何判定](guide:topic.permissionResolution)、[MESA 如何决定](guide:topic.mesaRules)、[审批如何运作](guide:topic.approvalLifecycle)

## 为什么不用长期访问令牌 {#token}

Home Assistant 长期访问令牌会以你的用户身份在 Home Assistant 的所有界面中生效。任何持有它的人都能做你能做的事。

访问密钥只对 Phoenix 有效。它有自己的权限、可选的有效期，以及只作用于 Phoenix 自身连接的速率限制。你可以吊销它，而不必改动自己的登录。Phoenix 的连接不接受 Home Assistant 令牌。

## 从哪里开始 {#start}

在{{label:shell.tabAccessKeys}}工作区中，选择 **{{label:accessKeys.setUpAgent}}**。设置流程会带你创建一个密钥、设置其权限，并选择 Agent Chat 或外部应用，最后让你测试连接。

相关：[智能体如何连接](guide:wizard.choose)、[安装 Phoenix MCP](guide:topic.install)
