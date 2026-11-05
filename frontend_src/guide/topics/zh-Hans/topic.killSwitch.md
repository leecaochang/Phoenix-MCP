---
{ "id": "topic.killSwitch", "title": "紧急停止开关会停止什么", "parent": "window.settingsSafety", "order": 1, "keywords": ["紧急", "停止", "禁用", "503", "恐慌"] }
---
紧急停止开关会切断 AI 通过 Phoenix 接触你家的所有途径。它不会撤销已经发生的操作。

## 什么会停止 {#stops}

- 通过 MCP 连接的外部应用会被拒绝。已在运行的请求会在下一次检查时停止。
- Agent Chat 会关闭，且无法再次打开。
- 语音智能体只会回复它不可用。
- AI 任务实体会变为不可用。
- Assist 工具提供商不再提供任何工具。
- 批准等待中的请求会改为取消它。配置恢复审核是例外：它仍会运行。

## 什么继续工作 {#keeps}

此面板继续工作，以便你查看发生了什么并重新关闭开关。你仍然可以在变更中还原更改、编辑 MESA 策略，上下文中的策略按钮也会保留。

## 重启之后 {#restart}

如果 Home Assistant 启动时开关处于开启状态，Phoenix 根本不会打开其连接。关闭开关会打开连接，无需重启。

相关：[紧急停止开关](guide:window.settingsSafety#kill-switch)
