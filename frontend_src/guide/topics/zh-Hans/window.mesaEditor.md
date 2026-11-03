---
{ "id": "window.mesaEditor", "title": "{{label:workspace.profileEditor}}", "parent": "workspace.mesa", "order": 2, "keywords": ["MESA", "编辑策略"], "sectionKeywords": { "target": ["实体", "设备", "区域", "集成", "域"], "tags": ["语义标签", "能力标签"], "control-mode": ["自主", "确认", "只读", "禁止", "未设置", "审批"], "enforcement": ["建议", "强制执行"], "triggers-automations": ["可能", "触发校验"], "effect": ["可撤销", "撤销代价", "副作用范围"], "privacy-level": ["公开", "普通", "敏感", "受限"], "save": ["放弃", "未保存"], "effective-resolution": ["继承", "来源", "来自", "解析告警"], "from-home-assistant": ["对话框", "上下文", "加号按钮"] } }
---
你在[{{label:workspace.mesaProfiles}}](guide:window.mesaProfiles)中选择的策略，或一个新策略。标题说明它是什么类型的策略以及覆盖什么。

## 覆盖范围 {#target}

对于新策略，从列表中选择它覆盖的内容。已经有此类策略的目标不会提供。策略保存后，其目标无法更改。

## {{label:mesa.semanticTags}} {#tags}

标准的 MESA 标签，告诉智能体设备是什么、能做什么，例如门锁或加热器。标签不会改变智能体可以做什么。最多添加 6 个。**{{label:mesa.showSuggestions}}** 会列出适合该域的标签。

## {{label:mesa.fieldControlMode}} {#control-mode}

智能体可以如何更改策略覆盖的内容：

- **{{label:mesa.modeAutonomous}}**：智能体可以自由操作。
- **{{label:mesa.modeConfirm}}**：MESA 强制执行时，操作会等待你的审批。
- **{{label:mesa.modeReadOnly}}**：除关闭外的任何 MESA 模式下，智能体都不能更改它。
- **{{label:mesa.modeProhibited}}**：MESA 强制执行时，智能体不能对它操作。在 {{label:settings.mesaModeAdvisory}} 模式下，它只会警告智能体。
- **{{label:mesa.unset}}**：策略不做决定。由其他策略或内置基线决定，列表会显示 **{{label:mesa.modeInherited}}**。

新建的实体策略从实体当前的模式开始。其他新策略从 {{label:mesa.unset}} 开始。

设置模式会替换内置基线，即使模式更宽松。确认的锁域策略会让每把锁变为确认，而不是禁止。

相关：[策略如何组合](guide:topic.mesaRules#combining)、[内置基线](guide:topic.mesaRules#baseline)

## {{label:mesa.fieldEnforcement}} {#enforcement}

当设置中的 **{{label:settings.mesaModeLabel}}** 为 {{label:settings.mesaModeAdvisory}} 时，**{{label:mesa.modeEnforced}}** 会强制执行此策略。可用于只对一个设备严格，而不让 MESA 在所有地方都严格。设置为关闭时，不会强制执行任何内容。

**{{label:mesa.modeAdvisory}}**（默认）交由设置决定。它不会被保存，因此更宽泛且设为强制的策略仍然适用。

相关：[操作违反规则时会发生什么](guide:topic.mesaRules#modes)

## {{label:mesa.fieldTriggersAutomations}} {#triggers-automations}

更改此项是否可能启动自动化。MESA 会用它对自动化进行检查：标记为 **{{label:mesa.trigNone}}** 但仍被自动化使用时，会在[{{label:workspace.advisory}}](guide:window.mesaAdvisory#validation)中显示一条发现。默认：**{{label:mesa.trigUnknown}}**。

## 描述效果 {#effect}

**{{label:mesa.fieldReversible}}**、**{{label:mesa.fieldReversibilityCost}}** 和 **{{label:mesa.fieldSideEffectScope}}** 告诉智能体更改是否可以撤销、撤销的代价，以及效果影响的范围。它们为智能体提供信息，但不会阻止任何操作。每项默认都是 {{label:mesa.unset}}。

## {{label:mesa.fieldPrivacyLevel}} {#privacy-level}

此策略向智能体公开的数据有多敏感。默认：**{{label:mesa.privNormal}}**。**{{label:mesa.privRestricted}}** 会让自主策略需要确认。

相关：[隐私](guide:topic.mesaRules#privacy)

## 保存 {#save}

**{{label:mesa.save}}** 会保存策略。它会立即生效。对于已有策略，在你更改内容后保存才可用。

**{{label:mesa.discardChanges}}** 会把表单恢复为已保存的状态，或关闭新策略。在列表中选择另一条策略也会替换表单，因此未保存的更改会丢失。

保存会用表单显示的内容替换整个策略。表单不显示的字段，例如导入的声明限制或时间窗口，会被移除。供应商策略会变为你自己的策略。

相关：[策略编辑器无法设置什么](guide:topic.mesaRules#not-in-editor)

### 触发警告 {#save-warnings}

保存实体策略后，Phoenix 会用它对自动化进行检查。如果发现问题，策略仍会保存，警告会列在表单下方。选择 **{{label:mesa.dismiss}}** 可关闭它们。

### {{label:mesa.delete}} {#delete}

在你就地确认后移除策略。它覆盖的内容随后遵循其他策略，或内置基线。

## {{label:mesa.effectiveResolution}} {#effective-resolution}

对于已保存的实体策略，这是合并所有层级后 MESA 实际使用的值。**{{label:mesa.colFrom}}** 注明每个值来自哪个层级。**{{label:mesa.colOrigin}}** 说明由谁写入：user 表示你，developer 表示集成供应商策略。

其上的 **{{label:mesa.resolutionWarnings}}** 表示有规则未能按原样生效，例如 MESA 忽略了某个覆盖。警告来自 MESA 本身，且为英文。

## 来自 Home Assistant 的页面 {#from-home-assistant}

开启策略按钮后，同一个编辑器会从 Home Assistant 自己的页面以对话框形式打开，针对你正在查看的实体、设备、区域或集成。取消时会先询问，再丢弃你的更改。

相关：[{{label:settings.injectLabel}}](guide:window.settingsGeneral#inject)
