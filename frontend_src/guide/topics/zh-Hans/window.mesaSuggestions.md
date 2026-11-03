---
{ "id": "window.mesaSuggestions", "title": "{{label:mesa.suggestTitle}}", "parent": "workspace.mesa", "order": 3, "keywords": ["MESA", "建议", "未受保护", "影响范围"], "sectionKeywords": { "why": ["锁", "警报", "车库", "阀门", "警报器", "热水器", "固件", "自动化", "脚本", "场景"], "review": ["应用", "接受"], "dismiss": ["隐藏", "恢复", "重新扫描"] } }
---
可能需要 MESA 策略的设备和自动化。Phoenix 绝不会自行应用建议。指向某一行，可在快速帮助中阅读建议的原因。

## 为什么建议 {#why}

**{{label:mesa.signalUnprotected}}**：没有任何层级策略覆盖的风险设备。锁和警报控制面板会被建议为 {{label:mesa.modeProhibited}}。车库门、门和闸门、阀门、警报器、热水器和固件更新会被建议为 {{label:mesa.modeConfirm}}。

当同一类型有 5 个或更多未被覆盖时，会合并为一条域建议，车库门、门和闸门除外。

**{{label:mesa.signalBlastRadius}}**：没有策略、且使用风险设备或 8 个或更多实体的自动化、脚本或场景。它会被建议为 {{label:mesa.modeConfirm}}，这样智能体在启动它之前会先询问。

以模板表示的目标无法被看到，因此永远不会被建议。

## 处理建议 {#review}

选择一行，在[{{label:workspace.profileEditor}}](guide:window.mesaEditor)中作为新策略打开，并填入建议的模式。检查它，按需修改，然后选择 **{{label:mesa.save}}**。保存后建议会消失。

## {{label:mesa.dismiss}} {#dismiss}

对使用此家庭的所有人隐藏你不想要的建议。选择 **{{label:mesa.dismiss}}**，然后选择 **{{label:common.confirm}}**。

要再次看到已忽略的建议，请在 MESA 操作菜单中选择 **{{label:mesa.restoreDismissed}}**。同一菜单中的 **{{label:mesa.rescan}}** 会立即查找新建议；否则列表每 30 秒更新一次。
