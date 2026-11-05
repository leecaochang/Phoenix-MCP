---
{ "id": "window.settingsSafety", "title": "{{label:workspace.safety}}", "parent": "workspace.settings", "order": 4, "sectionKeywords": { "kill-switch": ["紧急", "停止", "禁用"], "mesa-mode": ["MESA", "建议", "强制", "执行"], "data-management": ["清除", "删除", "重置", "擦除"] } }
---
在紧急情况下停止 Phoenix，选择 MESA 保护设备的严格程度，并删除 Phoenix 的数据。

## {{label:settings.killSwitchCard}} {#kill-switch}

**{{label:settings.killSwitchDisableLabel}}** 会立即切断 AI 通过 Phoenix 接触你家的所有途径，无需确认。关闭它以恢复访问。无需重启。

开启时，工具栏上的警告按钮会引导你回到此开关。

相关：[紧急停止开关会停止什么](guide:topic.killSwitch)

## {{label:settings.mesaModeLabel}} {#mesa-mode}

当操作违反策略规则时，Phoenix 的设备安全层 MESA 会做什么。默认：{{label:settings.mesaModeAdvisory}}。

- {{label:settings.mesaModeOff}}：跳过 MESA，所有操作都被允许。
- {{label:settings.mesaModeAdvisory}}：大多数规则只会警告。隐私规则、只读设备和设为强制的策略仍会阻止。
- {{label:settings.mesaModeEnforced}}：禁止的操作会被阻止，需要确认的操作会等待你的审批。

在任何层级都没有 MESA 策略的设备遵循内置基线：灯被允许，锁和警报控制面板被禁止，其他所有都需要审批。导入的部署默认设置会替换该基线。在依赖 {{label:settings.mesaModeEnforced}} 之前，请先添加策略。

相关：[MESA](guide:workspace.mesa)

## {{label:settings.dataCard}} {#data-management}

删除 Phoenix 的数据。选择要删除的内容，选择 **{{label:settings.wipeSubmit}}**，然后输入 WIPE 确认。之后页面会重新加载。

清除无法撤销。有已批准请求仍在运行时，清除会被拒绝。

### {{label:settings.wipeCoreLabel}} {#wipe-core}

删除所有访问密钥、待处理审批和实体提示，并把所有设置重置为默认值。这还会关闭紧急停止开关。

它还会删除审计日志、对话日志、变更历史、路由统计和速率限制计数。它还会移除 Phoenix 创建的语音助手，除非它是 Home Assistant 唯一的助手。Agent Chat 对话也会被删除，但仅限此浏览器。

安全副本、日历更改记录和仪表盘资源记录会保留。要移除它们，请参阅[完全移除 Phoenix](guide:topic.security#removing)。

### {{label:settings.wipeProvidersLabel}} {#wipe-providers}

删除所有 AI 和决策提供商账户及其密钥。语音智能体、AI 任务和命令路由会失去账户，Phoenix 会移除它创建的语音助手。

### {{label:settings.wipeMesaLabel}} {#wipe-mesa}

删除所有层级的每条 MESA 策略，以及你忽略的建议。默认关闭，因此除非你选择此项，清除会保留你的设备规则。
