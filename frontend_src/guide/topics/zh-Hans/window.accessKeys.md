---
{ "id": "window.accessKeys", "title": "{{label:shell.tabAccessKeys}}", "parent": "workspace.accessKeys", "order": 1, "keywords": ["访问密钥", "令牌", "Bearer", "机密"], "sectionKeywords": { "create-key": ["新密钥", "添加密钥"], "key-expiry": ["过期", "有效期"], "rotate": ["新机密", "泄露", "丢失密钥"], "revoke": ["禁用", "移除密钥", "删除密钥"], "reconnect": ["工具已更改"] } }
---
你的访问密钥。选择一个密钥，即可在其他窗口中显示其设置。在选择密钥之前，只显示此列表。

## 列表 {#key-list}

每行显示密钥的名称、模式和状态，以及创建、最后更改、过期和最后使用的时间。已吊销和已过期的密钥会移到[{{label:accessKeys.archivedTitle}}](guide:window.archivedAccessKeys)。

在列表上方的筛选框中输入，可按名称或状态查找密钥。你可以排序、移动、调整大小和隐藏列；布局会保存在此浏览器中。

模式列显示限定范围，跳过权限树的密钥则显示 {{label:accessKeys.passThrough}}。

相关：[直通模式会开放什么](guide:topic.passThrough)

## {{label:accessKeys.createAccessKey}} {#create-key}

工具栏上的 **{{label:accessKeys.createAccessKey}}** 会在列表上方打开一个简短表单。再次选择它会关闭表单。

### {{label:accessKeys.nameLabel}} {#key-name}

3 到 32 个字母、数字、下划线或连字符。每个名称必须唯一，忽略大小写，连字符与下划线视为相同。名称会出现在审计日志和审批中，因此请说明哪个智能体使用该密钥。

### {{label:accessKeys.expiryLabel}} {#key-expiry}

永不过期（默认），或若干分钟、小时、天或周。密钥过期时，Phoenix 会将其归档。密钥创建后无法更改有效期。

### 速率限制 {#key-rate-limits}

**{{label:accessKeys.rateRequestsLabel}}**（默认 60）和 **{{label:accessKeys.rateBurstLabel}}**（默认 10）。两者之后都可以更改。

相关：[速率限制](guide:window.keySettings#rate-limiting)

### {{label:accessKeys.passThroughMode}} {#key-pass-through}

让密钥跳过权限树，从而可以访问所有实体。默认关闭。创建前请开启确认。

相关：[直通模式会开放什么](guide:topic.passThrough)

### 创建密钥之后 {#key-created}

Phoenix 会询问你将如何使用新密钥：

- **{{label:accessKeys.postCreateConnectTitle}}**：你的应用的设置步骤，已填入密钥。
- **{{label:accessKeys.postCreateChatTitle}}**：打开设置，以便在 Agent Chat、语音智能体或 AI 任务中使用该密钥。不会显示密钥的值。
- **{{label:accessKeys.postCreateShowTitle}}**：密钥的值，供复制。

在关闭此对话框前复制密钥。Phoenix 只存储它的哈希，无法再次显示。如果丢失，请轮换密钥。

## {{label:workspace.keyActions}} {#key-actions}

工具栏上的 **{{label:workspace.keyActions}}** 会作用于所选密钥。除重命名外，每个操作都会在列表上方要求你确认。

### {{label:workspace.rename}} {#rename}

把密钥名称变成文本框。按 Enter 保存，或按 Esc 取消。你也可以双击名称。通常的名称规则适用。

### {{label:accessKeys.rotate}} {#rotate}

为密钥提供新的机密值并显示它。其他所有内容保持不变，包括其权限和历史。旧值会立即失效，因此请更新使用它的每个应用。同一对话框中的 **{{label:accessKeys.helpConnect}}** 会显示每个应用使用新值的设置。

### {{label:accessKeys.enablePtLabel}} {#enable-pass-through}

让密钥跳过权限树。权限树会被保留：在 {{label:accessKeys.permissionsTree}} 窗口中选择 **{{label:accessKeys.switchToScoped}}** 可重新启用它。Home Companion 密钥不能使用直通模式。

相关：[直通模式会开放什么](guide:topic.passThrough)

### {{label:accessKeys.revoke}} {#revoke}

永久结束密钥。它的请求会被拒绝，等待中的审批会被取消。Phoenix 还会将其从语音智能体、AI 任务和 Assist 工具提供商中移除，并移到 {{label:accessKeys.archivedTitle}}。此操作无法撤销。

## 重新连接智能体 {#reconnect}

当你更改密钥可以使用的工具时，列表上方的横幅会要求你重新连接其智能体。有些应用只在连接时读取工具列表。横幅只对使用过的密钥显示。

在权限树中首次给密钥写入权限，或移除其最后一个写入权限，也会更改其工具，并同样显示横幅。
