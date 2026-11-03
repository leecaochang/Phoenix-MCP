---
{ "id": "workspace.mesa", "title": "MESA 工作区", "order": 40, "keywords": ["MESA", "安全", "策略", "控制模式"] }
---
MESA 是 Phoenix 的设备安全层。策略告诉 MESA 对某个设备或实体要有多谨慎：让智能体自由操作、先询问你，或完全不让它们碰。MESA 在每个访问密钥允许的范围之上生效。

- **{{label:workspace.mesaProfiles}}** 列出所有策略。选择一个即可打开，或选择 **{{label:mesa.addProfile}}** 新建策略。可在 MESA 操作菜单中导入和导出策略。
- **{{label:workspace.profileEditor}}** 显示所选策略。修改后保存。
- **{{label:mesa.suggestTitle}}** 列出可能需要策略的设备和自动化。在你保存策略之前，什么都不会改变。
- **{{label:workspace.advisory}}** 仅在 MESA 有发现要展示时出现。

MESA 执行的严格程度由 [设置](guide:window.settingsSafety#mesa-mode)中的 **{{label:settings.mesaModeLabel}}** 决定。默认的 {{label:settings.mesaModeAdvisory}} 模式下，大多数规则只会向智能体发出警告。

相关：[MESA 如何决定](guide:topic.mesaRules)、[Home Assistant 页面上的策略按钮](guide:window.settingsGeneral#inject)
