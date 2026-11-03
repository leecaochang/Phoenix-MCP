---
{ "id": "window.conversationLog", "title": "{{label:conversations.title}}", "parent": "workspace.logs", "order": 2, "keywords": ["对话", "聊天历史", "记录"], "sectionKeywords": { "recording": ["记录对话", "开启", "关闭"], "list": ["提示词", "轮次", "工具调用", "更改", "加载更多"], "filters": ["搜索", "来源", "结果", "日期"], "updates": ["暂停", "刷新"], "clear": ["删除历史", "清除"] } }
---
智能体说过和做过的事情，最新的在前。同一聊天中的 Agent Chat 轮次会显示为一个对话。

相关：[日志记录的内容](guide:topic.logRecords#conversation-sources)

## 开启记录 {#recording}

在你于[{{label:workspace.logsNotifications}}](guide:window.settingsLogs#conversation-log)中开启 **{{label:conversations.enable}}** 之前，日志是空的。选择工具栏上的 **{{label:logs.logSettings}}** 可在此打开它。记录默认关闭。

## 列表 {#list}

每行显示最先询问的内容、结果、来源，以及它包含多少轮次、工具调用和更改。选择一行，即可在[{{label:workspace.logDetails}}](guide:window.logDetails)中阅读它。

显示工具名称而不是提示词的行，是来自 MCP 客户端、Assist 或 Home Companion 的工具调用。它们不会传递用户说的话。

列表显示 25 个对话；选择 **{{label:common.loadMore}}** 加载更多。按最新优先之外的任何顺序排序，只会对已加载的行排序。

## {{label:logs.filters}} {#filters}

按来源、结果和日期缩小列表范围。搜索会查找任何已记录文本：说过的话、工具名称和参数以及结果。对话中任意位置匹配，都会显示整个对话。

隐藏筛选会停止应用它们，直到你再次显示。

## 实时更新 {#updates}

列表显示期间每 5 秒更新一次，日志详情中打开的对话也是如此。**{{label:logs.pauseUpdates}}** 会让它保持不动；暂停期间，**{{label:common.refresh}}** 会加载一次最新内容。

## {{label:conversations.clear}} {#clear}

删除所有已记录的对话，无论筛选显示什么。就地确认后继续。此操作无法撤销，审计日志会记录你执行了此操作。
