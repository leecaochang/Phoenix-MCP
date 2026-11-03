---
{ "id": "window.mesaProfiles", "title": "{{label:workspace.mesaProfiles}}", "parent": "workspace.mesa", "order": 1, "keywords": ["MESA", "策略列表"], "sectionKeywords": { "add": ["新策略", "创建"], "filters": ["搜索", "类型", "控制模式", "执行模式", "继承"], "list": ["列", "供应商", "来源", "标签"], "delete": ["移除"], "actions": ["重新扫描", "恢复已忽略"], "import": ["存档", "备份", "替换", "上传"], "export": ["存档", "备份", "下载"] } }
---
每条 MESA 策略，无论覆盖什么，都在一个列表中。列表显示期间每 30 秒更新一次。

## {{label:mesa.addProfile}} {#add}

在[{{label:workspace.profileEditor}}](guide:window.mesaEditor)中开始新建策略。选择它覆盖的内容：实体、设备、区域、集成或域。在你于那里选择 **{{label:mesa.save}}** 之前，不会保存任何内容。

相关：[层级](guide:topic.mesaRules#levels)

## {{label:logs.filters}} {#filters}

按 **{{label:mesa.colAppliesTo}}**、**{{label:mesa.colControl}}** 和 **{{label:mesa.fieldEnforcement}}** 缩小列表范围。每个选项会显示匹配多少条策略。**{{label:mesa.modeInherited}}** 表示该策略未设置控制模式。

搜索会匹配 ID、名称、类型和标签，并在筛选隐藏时继续有效。隐藏筛选会停止应用它们，直到你再次显示。更改筛选会关闭已打开的策略。

## 列表 {#list}

选择一条策略，即可在[{{label:workspace.profileEditor}}](guide:window.mesaEditor)中打开它。策略被强制执行时，控制模式旁会显示“{{label:mesa.modeEnforced}}”。

**{{label:mesa.colTags}}** 和 **{{label:mesa.colSource}}** 最初隐藏；可从列菜单中显示。**{{label:mesa.colSource}}** 对集成随代码提供的策略显示 **{{label:mesa.vendorBadge}}**。

## {{label:mesa.delete}} {#delete}

移除策略。选择 **{{label:mesa.delete}}**，然后选择 **{{label:common.confirm}}**，或点击其他任意位置退出。它覆盖的内容随后遵循其他策略，或者如果没有剩余策略，则遵循内置基线。

集成自己的供应商策略会在 Home Assistant 下次启动时恢复，除非你已为该集成保存了自己的策略。

## MESA 操作 {#actions}

菜单包含：

- **{{label:mesa.importTitle}}** 和 **{{label:mesa.exportTitle}}**，见下文。
- **{{label:mesa.suggestTitle}}**，用于显示或隐藏该窗口。
- **{{label:mesa.rescan}}**，会立即查找新的[建议](guide:window.mesaSuggestions)和建议性提示中的发现。
- **{{label:mesa.restoreDismissed}}**，会恢复你忽略的所有建议。
- **{{label:mesa.restoreNotices}}**，会恢复你在[{{label:workspace.advisory}}](guide:window.mesaAdvisory)中忽略的发现。

## {{label:mesa.importTitle}} {#import}

从 MESA 存档文件（例如你导出的文件）中添加策略。选择 **{{label:mesa.browse}}** 并选择文件。Phoenix 会在导入前显示存档包含的内容。

这里已存在的策略会保持原样。开启 **{{label:mesa.replaceExisting}}** 可改为覆盖它们；按钮会变红并显示 **{{label:mesa.importAndReplace}}**。不会再要求确认。

导入的策略会立即生效。Phoenix 会检查每条策略，并列出它拒绝的策略。针对此家庭没有的目标的策略会出现在[{{label:workspace.advisory}}](guide:window.mesaAdvisory#orphans)中。

存档可以包含策略编辑器无法设置的字段，例如声明限制、时间窗口和部署默认设置。

相关：[策略编辑器无法设置什么](guide:topic.mesaRules#not-in-editor)

## {{label:mesa.exportTitle}} {#export}

把所有策略下载为一个存档文件，如有部署默认设置也会包含。选择 **{{label:mesa.export}}** 以确认。该文件包含你完整的智能体安全策略，请妥善保存。
