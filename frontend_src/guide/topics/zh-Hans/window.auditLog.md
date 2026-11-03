---
{ "id": "window.auditLog", "title": "{{label:shell.tabAudit}}", "parent": "workspace.logs", "order": 1, "keywords": ["审计", "请求", "历史"], "sectionKeywords": { "list": ["列", "排序", "加载更多", "MESA 建议", "来源"], "filters": ["结果", "访问密钥", "时间", "方法", "资源", "请求 ID", "搜索"], "updates": ["暂停", "刷新", "实时"], "attention": ["需要关注", "在审计日志中显示"] } }
---
智能体和管理员通过 Phoenix 采取的每个操作，最新的在前。

相关：[日志记录的内容](guide:topic.logRecords)

## 列表 {#list}

选择一个条目，即可在[{{label:workspace.logDetails}}](guide:window.logDetails)中阅读它。上下方向键可在列表中移动。

- **{{label:audit.rowAccess}}** 说明密钥是使用了其权限还是[直通模式](guide:topic.passThrough)。
- **{{label:audit.rowMesa}}** 在 MESA 警告智能体但允许操作运行时显示 {{label:mesa.modeAdvisory}}。
- **{{label:audit.rowIp}}** 是调用方的 IP 地址，或发起调用的 Phoenix 功能，例如 Agent Chat。
- **{{label:audit.colAccessKey}}** 显示密钥的当前名称，即使此后已重命名。

日志一次加载 100 个条目；选择 **{{label:common.loadMore}}** 加载更多。按最新优先之外的任何顺序排序，只会对已加载的条目排序。

从列标题的菜单排序、移动和隐藏列，拖动标题边缘可调整列宽。

## {{label:logs.filters}} {#filters}

按 **{{label:audit.rowOutcome}}**、**{{label:audit.colAccessKey}}** 和时间，以及 **{{label:audit.rowMethod}}**、**{{label:audit.rowResource}}** 和 **{{label:audit.rowIp}}** 中的文本缩小列表范围。**{{label:audit.rowRequestId}}** 可查找一个确切的请求，例如来自对话日志的请求。像过去 5 分钟这样的时间窗口会随日志更新而移动。

隐藏筛选会停止应用它们，直到你再次显示。两个日志都打开时，一个 **{{label:logs.filters}}** 按钮会显示两者的筛选条件。

## 实时更新 {#updates}

窗口显示且未折叠时，新条目每 5 秒出现一次。**{{label:logs.pauseUpdates}}** 会在你阅读时让列表保持不动。暂停期间，**{{label:common.refresh}}** 会加载一次最新条目。

## 来自需要关注 {#attention}

需要关注窗口中的 **{{label:attention.showInAudit}}** 会打开此日志，并筛选到那一个请求。
