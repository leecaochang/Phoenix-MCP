---
{ "id": "window.notifications", "title": "{{label:notifications.title}}", "parent": "topic.windows", "order": 21, "keywords": ["消息", "历史"], "sectionKeywords": { "toasts": ["浮层", "弹出", "忽略", "确认"], "history": ["清除通知", "显示通知历史"] } }
---
Phoenix 在你此次访问面板期间显示的消息。

## 消息 {#toasts}

当你在面板中完成某件事时，例如保存 MESA 策略，屏幕底部会显示一条短消息，持续几秒钟。指向它或把焦点移入其中可让它保持显示。**{{label:notifications.dismiss}}** 会隐藏它。

问题不会以这种方式显示。它们会出现在发生问题的窗口中。

## {{label:notifications.title}}窗口 {#history}

列出最近 30 条消息，最新的在前，并显示每条出现的时间；来自更早日期的会显示日期。它们会保留到你重新加载面板。**{{label:notifications.clear}}** 会移除一条。

窗口默认关闭。可从工作区选项中的 **{{label:notifications.showHistory}}** 打开。手机上不提供此功能，但消息仍会显示。
