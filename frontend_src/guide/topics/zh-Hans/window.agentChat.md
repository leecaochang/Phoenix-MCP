---
{ "id": "window.agentChat", "title": "{{label:agentchat.title}}", "parent": "workspace.my", "order": 5, "keywords": ["聊天", "智能体", "助手", "对话", "Shift+A"], "sectionKeywords": { "opening": ["打开", "快捷键", "Shift+A", "每个页面", "弹出"], "window": ["最小化", "最大化", "移动", "调整大小", "停靠", "关闭"], "options": ["详细", "时间戳", "页脚", "文字大小"], "connection": ["访问密钥", "提供商", "模型", "思考", "温度", "账户", "Anthropic", "Claude", "DeepSeek", "OpenAI", "ChatGPT", "GPT", "Gemini", "Google", "Grok", "xAI", "Groq", "Kimi", "Moonshot", "Meta", "Llama", "MiniMax", "Mistral AI", "NVIDIA NIM", "Xiaomi MiMo", "Tencent Cloud TokenHub", "OpenCode", "Ollama", "OpenRouter", "Infron", "Together AI", "Cerebras", "Fireworks AI", "Alibaba Cloud Model Studio", "Qwen", "DashScope", "Z.ai", "Zhipu", "GLM", "深度求索", "通义千问", "千问", "阿里云百炼", "百炼", "智谱", "月之暗面", "腾讯云", "小米", "谷歌"], "conversation": ["发送", "停止", "步骤", "处理中", "复制", "token", "用量"], "memory-warning": ["长消息", "上下文", "截断", "仍然发送", "新聊天", "记忆"], "check-in": ["暂停", "继续", "签到前步骤"], "images": ["附加", "图像", "照片", "粘贴", "图片"], "approvals": ["批准", "拒绝", "审核", "等待"], "clear": ["清除聊天记录", "/clear", "新对话"], "privacy": ["存储", "浏览器", "发送给提供商", "历史"] } }
---
与 AI 智能体谈论你的家。它通过你的某个访问密钥行动，因此只能做该密钥允许的事，每个操作都会像其他操作一样被检查和记录。

相关：[{{label:settings.agentcliCard}} 设置](guide:window.settingsChat)、[{{label:workspace.aiProviders}}](guide:window.settingsProviders)

## 打开 Agent Chat {#opening}

选择工具栏上的 **{{label:shell.agentChat}}**。在你有一个访问密钥和一个 AI 提供商账户后，它会出现。Shift+A 也可以打开和关闭它，除非你正在字段中输入。它需要你的 Home Assistant 个人资料中开启键盘快捷键。

**{{label:settings.agentcliGlobalLabel}}** 开启时（默认），你在 Home Assistant 中移动时 Chat 会保持打开。关闭时，Chat 位于 Phoenix 面板中，离开面板会结束正在进行的回复。

紧急停止开关开启时，Chat 会关闭且无法打开。

## 窗口 {#window}

拖动标题栏可移动它，从任意边缘可调整大小。靠近屏幕边缘时，边缘会吸附。最小化会把它缩成可移动的小条，最大化会填满屏幕。

**{{label:agentchat.popOut}}** 会把 Chat 移到它自己的浏览器窗口，对话会在那里继续。在手机上，Chat 会全屏打开。

在电脑上，你还可以把 Chat 停靠到[我的工作区](guide:workspace.my#chat)。关闭 Chat 不会清除对话。

## {{label:agentchat.options}} {#options}

标题栏中的菜单按钮：

- **{{label:agentchat.connection}}** 显示下面的连接字段。
- **{{label:agentchat.verbose}}** 显示智能体调用的每个工具、其结果，以及模型在分享时的推理过程。默认关闭。
- **{{label:agentchat.showTimestamps}}** 在每条消息下方添加时间。默认关闭。
- **{{label:agentchat.showFooter}}** 显示带有文字大小和 token 用量的页脚。默认开启。
- **{{label:agentchat.decreaseTextSize}}** 和 **{{label:agentchat.increaseTextSize}}** 更改对话文字的大小。

这些选择会保存在此浏览器中。

## {{label:agentchat.connection}} {#connection}

标题下方的行显示正在使用的访问密钥、提供商和模型。选择其三角形可更改它们。

- **{{label:agentchat.accessKey}}**：智能体按此密钥的权限工作。
- **{{label:agentchat.provider}}**：运行智能体的 AI 提供商账户。
- **{{label:agentchat.model}}**：要使用的模型，来自该账户的列表。
- **{{label:agentchat.thinking}}** 和 **{{label:agentchat.temperature}}**：仅在模型提供它们时显示。温度留空表示使用提供商的默认值。

更改访问密钥或提供商会开始新对话。回复运行期间，你无法更改密钥、提供商或模型。

## 与智能体交谈 {#conversation}

按 Enter 发送，按 Shift+Enter 换行。智能体工作时，一行会显示它在做什么，以及它等待提供商了多久。**{{label:agentchat.stop}}** 会结束回复，并把你的消息放回输入框。

如果回复期间连接中断，灰色提示会告诉你。只有在尚未收到任何内容时，你的消息才会放回输入框，因为智能体可能已经执行了操作。

用消息的复制按钮复制它。页脚显示此对话使用的 token，以及上一次请求占用了模型上下文的多大比例。

## 暂停确认 {#check-in}

在没有你输入的若干步骤后，智能体会暂停并询问是否继续。一个步骤是一次对模型的请求加上它请求的工具。选择 **{{label:agentchat.continue}}** 让它继续。

批准或拒绝操作会重置计数。在[{{label:settings.agentcliStepsLabel}}](guide:window.settingsChat#chat-steps)中设置数量。

## 长消息 {#memory-warning}

当发送一条消息会把你较早的消息挤出[{{label:settings.agentcliMemoryLabel}}](guide:window.settingsChat#chat-memory)时，Phoenix 会先询问，并说明模型将看不到多少条消息。

- **{{label:agentchat.sendAnyway}}** 发送消息。最早的消息仍留在屏幕上，但模型不再看到它们。
- **{{label:agentchat.sendInNewChat}}** 清除当前对话，并把这条消息作为新对话的第一条发送。
- **{{label:agentchat.editMessage}}** 关闭警告，以便你缩短消息。修改消息也会关闭它。

对话慢慢超出上限时不会询问。会有一条通知告诉你一次，最早的消息已不再发送。

## 图像 {#images}

粘贴图像、把图像拖到窗口上，或使用附加按钮：每条消息最多 4 张。Phoenix 会在发送前缩小大照片。缩小后，单张图像最大 2 MiB，一条消息的图像总计最大 4 MiB。

Home Companion 密钥无法发送图像，也无法发送给只读取文本的模型。重新加载后不会保留图像，因此智能体之后无法再次看到它们。

## 聊天中的审批 {#approvals}

操作需要你的审批时，对话中会出现一张卡片。选择 **{{label:agentchat.approve}}** 或 **{{label:agentchat.reject}}**，或选择 **{{label:agentchat.review}}** 先在审批中查看完整更改。

智能体最多等待 15 分钟。在审批中或从通知中批准也会更新卡片。停止、关闭 Chat 或重新加载会取消仍在等待的操作。

相关：[审批如何运作](guide:topic.approvalLifecycle)

## {{label:agentchat.clearHistory}} {#clear}

立即开始新对话，无需询问。输入 /clear 也会如此。对话日志会保留已记录的内容。

## 保留什么，以及在哪里 {#privacy}

每个 Home Assistant 用户在每个浏览器中有一个对话，保存在此浏览器中，因此重新加载后仍会存在。各标签页共用它。最新的消息（在[{{label:settings.agentcliMemoryLabel}}](guide:window.settingsChat#chat-memory)范围内）和工具结果会随每条消息发送给提供商。除非对话日志开启，否则 Phoenix 不在服务器上保留副本。

如果两个标签页同时运行回复，保留最后完成的那个。另一个标签页运行回复时，此标签页会在消息框上方提示。

很长的对话仍会显示在屏幕上，但最早的消息不再发送，并且会提示你一次。

对话连续 30 天未使用后，或你在此浏览器中删除核心数据时，Phoenix 会将其删除。其他浏览器会保留各自的对话。

非管理员在此浏览器中登录时，Phoenix 也会删除它，但前提是 **{{label:settings.agentcliGlobalLabel}}** 或 **{{label:settings.injectLabel}}** 已开启。两者都关闭时，Phoenix 不会为此人加载，对话会保留。

页面隐藏 5 分钟后，Home Assistant 会关闭它的连接，正在进行的回复也会中断。如需长时间回复，请让页面保持可见，或在 Home Assistant 个人资料中关闭该选项。主页面位于弹出的 Agent Chat 后面时同样如此。

相关：[哪些数据会离开你的家](guide:topic.security#leaves-home)

只有管理员可以使用 Agent Chat。
