---
{ "id": "wizard.connect", "title": "连接外部应用", "parent": "workspace.accessKeys", "order": 106 }
---
选择你的应用，获取确切的命令或配置，其中已填入此密钥的机密值。现在就复制：Phoenix 只存储密钥的哈希，无法再次显示该值。如果丢失，请轮换密钥以获取新的值。

对于 ChatGPT、Claude 或它们的手机应用，请选择 **{{label:wizard.methodOAuth}}**。这些应用通过 OAuth 登录：在那里开启 **{{label:oauth.switchLabel}}**，然后按照你的应用的步骤操作。不会复制任何机密内容。

**{{label:wizard.testConnection}}** 会等待应用的第一个请求。**{{label:wizard.connectLater}}** 会现在完成，把测试留到以后。

这一步会在你设置应用时监视此密钥。当应用使用此密钥登录，或智能体发送第一个请求时，Phoenix 会提示你，**{{label:wizard.next}}** 会跳过测试。如果审批对话框列出多个密钥，请选择这一个；最后打开登录的密钥排在最前面。
