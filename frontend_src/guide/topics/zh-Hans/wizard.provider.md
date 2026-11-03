---
{ "id": "wizard.provider", "title": "设置模型提供商", "parent": "workspace.accessKeys", "order": 104 }
---
Agent Chat 会使用你自己的账户，把对话发送给你选择的模型提供商。

选择一个已添加的账户，或添加一个：选择提供商，填写其字段，然后选择 **{{label:settings.agentcliValidate}}**。大多数提供商只需要 API 密钥；有些还会要求区域，本地 Ollama 服务器则需要其地址。

验证后，选择一个模型，或保留 **{{label:settings.agentcliModelAuto}}**。检查模型设置的选项默认开启，会消耗少量 API 额度。

**{{label:wizard.tryNow}}** 会保存新账户，然后用新密钥打开 Agent Chat，并已输入一个测试问题。在此之前不会保存任何内容。
