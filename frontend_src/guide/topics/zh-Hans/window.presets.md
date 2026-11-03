---
{ "id": "window.presets", "title": "{{label:accessKeys.cardPresets}}", "parent": "workspace.accessKeys", "order": 7, "keywords": ["快照", "设置组合", "切换设置"] }
---
预设是此密钥已保存的一套设置。在预设之间切换可快速更改密钥，例如一个谨慎的日常预设和一个范围更广的维护预设。当[{{label:workspace.general}}](guide:window.settingsGeneral#presets)中的 **{{label:settings.presetsLabel}}** 开启时，此窗口会出现。

预设包含密钥的角色、能力、权限树、直通模式、Assist 公开范围、工具声明、审批等待和速率限制。它不包含密钥的名称或指引。

每个密钥最多可以有 8 个预设。正在使用的预设标记为 **{{label:accessKeys.presetActive}}**。**{{label:accessKeys.presetModified}}** 表示密钥自该预设上次保存以来已发生变化。

## {{label:accessKeys.presetSwitchTo}} {#switch-to}

把密钥的当前设置保存到当前预设，然后应用你选择的预设。会先显示更改内容列表，供你确认。如果预设会开启直通模式，也要确认这一点。

切换会告知已连接的智能体其工具已更改，并重新开始密钥的速率限制计数。

## {{label:accessKeys.presetRevertToSaved}} {#revert}

在你确认后，丢弃自当前预设保存以来所做的更改。

## {{label:accessKeys.presetSaveCurrent}} {#save-current}

把密钥的当前设置保存为新预设，并使其成为当前预设。先输入最多 40 个字符的名称。你原来所在的预设仍会保留上次保存的状态。

## 重命名和删除 {#rename-delete}

就地编辑预设名称；按 Enter 保存或按 Esc 取消。**{{label:accessKeys.delete}}** 会在你确认后删除预设。你不能删除当前预设。
