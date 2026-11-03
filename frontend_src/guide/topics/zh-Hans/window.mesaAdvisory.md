---
{ "id": "window.mesaAdvisory", "title": "{{label:workspace.advisory}}", "parent": "workspace.mesa", "order": 4, "keywords": ["MESA", "发现", "提示", "警告"], "sectionKeywords": { "inspection": ["模板", "蓝图", "部分检查"], "validation": ["触发", "条件", "触发自动化"], "orphans": ["孤立", "缺失", "已删除目标"], "dismiss": ["隐藏", "恢复提示"], "refresh": ["过期", "更新", "过时"] } }
---
MESA 在检查你的策略和自动化时注意到的事情。窗口仅在有事可展示时出现。这些内容都不会改变 MESA 对待操作的方式。

## 部分检查的自动化 {#inspection}

使用模板或蓝图的自动化，因此 MESA 无法读取它们触及的每个实体。打开 **{{label:mesa.viewAutomationDetails}}** 可列出它们。你不需要做任何事。

## 触发发现 {#validation}

某实体的策略说 **{{label:mesa.fieldTriggersAutomations}}** 为 {{label:mesa.trigNone}}，但有自动化使用了它。在触发器中这是错误，在条件中是警告。每条发现都会说明要更改什么，在策略中还是在自动化中。

相关：[{{label:mesa.fieldTriggersAutomations}}](guide:window.mesaEditor#triggers-automations)

## 孤立的策略 {#orphans}

其实体、设备、区域或集成已不存在的策略，例如在你移除设备之后。集成在没有任何实体且未加载时算作已消失。选择一条策略，即可在[{{label:workspace.profileEditor}}](guide:window.mesaEditor)中打开并删除它。Phoenix 绝不会替你删除它们。

## {{label:mesa.dismiss}} {#dismiss}

在此浏览器中隐藏一组发现。当新发现加入该组时，它会再次出现。在 MESA 操作菜单中选择 **{{label:mesa.restoreNotices}}** 可再次看到已忽略的发现。

## 发现何时更新 {#refresh}

MESA 会在 Home Assistant 启动、自动化重新加载，以及你在此保存、删除或导入策略时再次检查，因此已修复的发现会消失。**{{label:mesa.rescan}}** 会立即再次检查。
