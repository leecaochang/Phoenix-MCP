---
{ "id": "window.settingsLogs", "title": "{{label:workspace.logsNotifications}}", "parent": "workspace.settings", "order": 5, "keywords": ["日志记录", "记录"], "sectionKeywords": { "audit-log": ["审计"], "log-max-entries": ["保留"], "conversation-log": ["历史", "保留"], "privacy": ["隐去"], "log-client-ip": ["IP 地址"], "notifications": ["通知", "提醒"] } }
---
选择 Phoenix 记录什么、保留多久，以及 Home Assistant 何时通知你。此窗口也可以从{{label:workspace.logs}}工作区中的 **{{label:logs.logSettings}}** 打开。

## {{label:shell.tabAudit}} {#audit-log}

审计日志记录智能体通过 Phoenix 做了什么，以及管理员更改了什么，并记录各自的结果。

相关：[日志记录的内容](guide:topic.logRecords)

### {{label:settings.logEnableLabel}} {#log-enable}

记录审计条目和对话。关闭它会停止所有记录；其他日志设置随后会灰显。默认开启。

Phoenix 的传感器会继续计数，通知仍会到达。

### {{label:settings.logAllowedLabel}} {#log-allowed}

记录被执行的操作。默认开启。

### {{label:settings.logDeniedLabel}} {#log-denied}

记录被拒绝、格式错误、不受支持或指向不存在内容的请求。MESA 阻止也计在这里。默认开启。

等待审批的请求、你的审批决定，以及因应用离开或失去权限而中断的请求始终会被记录。

### {{label:settings.logRateLimitedLabel}} {#log-rate-limited}

记录因密钥发送过多而被拒绝的请求。默认开启。

这三个开关只影响新条目。已有条目保持不变。

### {{label:settings.flushIntervalLabel}} {#log-flush}

审计日志保存到磁盘的频率：每 5、10、15（默认）、30 或 60 分钟，或从不。Home Assistant 停止或 Phoenix 重新加载时也会保存。

### {{label:settings.maxLogEntriesLabel}} {#log-max-entries}

审计日志保留多少条目：100、1,000、5,000 或 10,000（默认）。降低它会立即移除最旧的条目。磁盘上的副本会在下次保存时缩小。

## {{label:conversations.title}} {#conversation-log}

**{{label:conversations.enable}}** 会保留智能体说了什么、做了什么：Agent Chat 和语音对话、AI 任务，以及 MCP 客户端、Assist 和 Home Companion 的工具调用。在{{label:workspace.logs}}工作区中查看。默认关闭。关闭它会保留已记录的内容。

三个限制控制保留多少内容。每个限制在你更改后片刻保存。日志超过限制时，最旧的对话会先被移除。

### {{label:conversations.limit.entries}} {#conversation-entries}

保留多少个对话：1 到 2,000（默认 100）。

### {{label:conversations.limit.days}} {#conversation-days}

对话保留多少天：1 到 365（默认 7）。

### {{label:conversations.limit.mb}} {#conversation-size}

日志可以占用多少空间：1 到 250 MB（默认 25）。当前使用的空间显示在限制下方。日志较大时，Home Assistant 启动时加载它所需的时间更长，内存占用也可能达到此大小的约两倍。

## {{label:settings.privacySection}} {#privacy}

选择日志显示关于你家和使用 Phoenix 的人的哪些信息。

### {{label:settings.logEntityIdsLabel}} {#log-entity-ids}

关闭时，审计日志的 {{label:audit.rowResource}} 列显示 [redacted]，对话日志会省略说了什么和做了什么。默认开启。

此时审计日志的负载只保留工具调用参数的名称，不保留它们的值。负载中的其他文本（例如审批原因）仍可能提到实体。已记录的对话内容会在 Phoenix 下次启动时移除。

### {{label:settings.logClientIpLabel}} {#log-client-ip}

关闭时，两种日志都会隐藏调用方的 IP 地址，但写入工具调用参数中的地址会留在审计负载中。默认开启。Agent Chat、语音、Assist、AI 任务和 Home Companion 仍会按名称显示，因为它们不是地址。

## {{label:settings.haNotifications}} {#notifications}

Phoenix 可以发送 Home Assistant 通知，它们会出现在 Home Assistant 侧边栏中。

### {{label:settings.notifyApprovalLabel}} {#notify-approval}

为每个等待你批准的请求发送一条带链接的通知。你做出决定后它会消失。默认开启。工具栏上的审批计数无论如何都会显示。

### {{label:settings.notifyRateLimitLabel}} {#notify-rate-limit}

当访问密钥发送请求的速度超过其限制时通知你。默认关闭。每个密钥有一条通知，每分钟最多更新一次。
