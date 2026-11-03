---
{ "id": "topic.mesaRules", "title": "MESA 如何决定", "parent": "workspace.mesa", "order": 10, "keywords": ["MESA", "策略", "安全", "继承", "优先级"], "sectionKeywords": { "levels": ["继承", "主设备", "父设备", "范围"], "combining": ["最严格", "冲突", "放宽", "覆盖"], "baseline": ["默认", "无策略", "内置", "部署默认设置"], "modes": ["建议", "强制执行", "关闭", "警告", "阻止"], "privacy": ["受限", "敏感", "拒绝"], "coverage": ["读取", "摄像头", "自动化", "直通模式"], "not-in-editor": ["声明限制", "时间约束", "时间窗口", "访问角色", "仅导入"] } }
---
在访问密钥允许之后，MESA 会检查智能体对设备采取的每个操作。策略描述对其覆盖范围要有多谨慎。本页说明不同层级的策略如何叠加，以及操作违反策略时会发生什么。

## 层级 {#levels}

一个策略覆盖五类对象之一。智能体对实体执行操作时，MESA 会收集所有覆盖它的策略，最具体的在前：

- **{{label:mesa.scopeLabelEntity}}**：实体本身。
- **{{label:mesa.scopeLabelDevice}}**：实体所属的设备，包括该设备之后获得的实体。
- **{{label:mesa.scopeLabelParentDevice}}**：对于属于更大设备一部分的设备，使用更大设备的策略。设备连接所经过的集线器不算。
- **{{label:mesa.scopeLabelArea}}**：实体所在的区域，否则是其设备所在的区域。
- **{{label:mesa.scopeLabelIntegration}}**：创建该实体的集成，例如 hue。
- **{{label:mesa.scopeLabelDomain}}**：同一类型的所有实体，例如所有锁。

除主设备外，你可以在 **{{label:workspace.profileEditor}}** 中为所有这些对象添加策略；主设备是从其组成部分看的一个设备策略。

## 策略如何组合 {#combining}

MESA 按字段组合策略，而不是按策略整体组合。

- **控制模式**：任一层级设置的最严格模式胜出。具体策略不能放宽更宽泛的策略，因此禁止的锁域下的自主锁实体仍为禁止。唯一的例外见下文。
- **隐私级别**：最严格的级别胜出，方式相同。
- **触发自动化**：任一层级为可能则胜出。否则最具体的层级胜出。
- **其他所有项**：最具体的层级胜出，但由人写入或确认的值，或由设备制造商写入的值，优先级高于任何层级推断出的值。

放宽控制模式的唯一方式是带书面原因的实体覆盖，它可以把确认变为自主，但绝不会放宽禁止或只读。策略编辑器无法设置它；它只能通过导入出现。

相关：[生效值解析](guide:window.mesaEditor#effective-resolution)

## 内置基线 {#baseline}

没有任何策略覆盖的实体遵循内置基线：灯为自主，锁和警报控制面板为禁止，其他所有为确认。

策略会改变这一点：

- **设置控制模式的策略会替换基线**，无论它位于哪个层级，即使更宽松。锁上的确认策略会让锁变为确认，而不是禁止。
- **不设置控制模式的策略保留基线**，但原本自主的都会变为确认。灯上的仅标签策略会让该灯需要确认。

因此，针对锁的自主域策略会让每把锁变为自主。在依赖某个策略之前，请在[生效值解析](guide:window.mesaEditor#effective-resolution)中检查它的效果。

部署默认设置可以为没有策略的实体替换内置基线。它们来自导入的存档；策略编辑器无法编辑它们。

## 操作违反规则时会发生什么 {#modes}

MESA 的行为取决于[设置](guide:window.settingsSafety#mesa-mode)中的 **{{label:settings.mesaModeLabel}}**，以及策略自身的 **{{label:mesa.fieldEnforcement}}**。

- **{{label:settings.mesaModeOff}}**：MESA 允许一切。
- **{{label:settings.mesaModeAdvisory}}**（默认）：被禁止的操作和违反的限制只会警告智能体，确认操作无需审批即可运行。只读实体和隐私拒绝仍会阻止。
- **{{label:settings.mesaModeEnforced}}**：被禁止、只读的操作和违反的限制会被阻止。确认操作会在[审批](guide:topic.approvalLifecycle)中等待你的审批。

设为 **{{label:mesa.modeEnforced}}** 的策略，在设置为建议模式时仍会强制执行。设置为关闭时，不会强制执行任何内容。

操作针对多个实体时，MESA 会丢弃被阻止的实体，操作在其余实体上运行。警告或阻止会连同原因一起传给智能体。

## 隐私 {#privacy}

**{{label:mesa.privRestricted}}** 隐私级别会让自主实体需要确认。人员始终至少为 **{{label:mesa.privSensitive}}**。

导入的策略还可以拒绝对某些角色的访问。这种拒绝会在除关闭之外的每种模式下阻止操作，包括摄像头图像。

## MESA 覆盖什么 {#coverage}

MESA 覆盖智能体更改内容的所有方式：MCP 客户端、Agent Chat、语音智能体、Assist 和 AI 任务。它也适用于直通访问密钥。

它检查设备操作、重命名和移除实体与设备、集成和辅助元素更改、区域和楼层、日历写入以及通知。

读取状态不会被检查，摄像头图像除外。自动化和脚本在 Home Assistant 内部运行，因此它们的操作不会被再次检查。要控制智能体何时启动自动化或脚本，请为它们设置自己的策略。

## 策略编辑器无法设置什么 {#not-in-editor}

策略还可以包含声明限制（例如最高温度）、适用更严格规则的时间窗口、对某些角色的隐私拒绝，以及实体覆盖。这些只能通过导入的存档出现。

在策略编辑器中保存策略，会用表单显示的内容替换整个策略，因此会移除这些字段。

相关：[{{label:mesa.importTitle}}](guide:window.mesaProfiles#import)
