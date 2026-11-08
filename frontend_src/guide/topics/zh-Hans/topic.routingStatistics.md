---
{ "id": "topic.routingStatistics", "title": "阅读路由统计", "parent": "window.settingsChat", "order": 2, "keywords": ["计数", "百分比", "token", "用量", "重置"] }
---
路由统计显示 Phoenix 有多少次在没有使用你的 AI 模型的情况下执行了命令，以及其余部分花费了多少。Agent Chat、语音智能体和“评估”操作分别计数。

## 总体数字 {#headline}

顶部的百分比是由句子匹配或决策提供商处理的消息占比。它越高，你支付的完整模型轮次就越少。

## 计数 {#counts}

每条消息按其结果计数一次。如果某条消息在任何时候使用了你的 AI 模型，它就算作发送给了该模型。

- **{{label:routingStats.local}}**：由 Home Assistant 的句子匹配完成。
- **{{label:routingStats.decision}}**：由决策提供商完成。
- **{{label:routingStats.provider}}**：由你的 AI 模型处理。
- **{{label:routingStats.pending}}**：等待你的审批。
- **{{label:routingStats.failed}}** 和 **{{label:routingStats.cancelled}}**：未完成的消息。

发送给你的 AI 模型的消息也可能失败或被取消，因此各行相加不一定等于总数。

## 评估 {#evaluate}

“评估”窗口统计 [Decisions 实体](guide:window.settingsHome#ai-decisions)处理的“评估”操作。顶部数字是调用次数，因为每次调用都会发送给决策提供商。数据生成不计入。

- **{{label:routingStats.evaluated}}**：决策提供商给出了回答。
- **{{label:routingStats.refused}}**：实体拒绝了这次调用，例如发起者对它没有控制权限，或已达到请求上限。
- **{{label:routingStats.failed}}** 和 **{{label:routingStats.cancelled}}**：未完成的调用。

它只显示决策 token。

## Token {#tokens}

Token 行汇总决策提供商和你的 AI 模型报告的用量。提供商并不总是报告用量。没有报告的请求会计入总数下方。

## 刷新和重置 {#refresh-and-reset}

**{{label:common.refresh}}** 会加载最新计数。**{{label:routingStats.reset}}** 会在你确认后把这个窗口的计数归零。重置后，仍在处理中的消息不会被计入。

计数每 30 秒保存一次，重启后仍会保留。在[数据管理](guide:window.settingsSafety#wipe-core)中删除核心数据会清除它们。
