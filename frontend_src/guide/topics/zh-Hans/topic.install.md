---
{ "id": "topic.install", "title": "安装 Phoenix MCP", "parent": "topic.about", "order": 10, "keywords": ["安装", "设置", "HACS", "要求"], "sectionKeywords": { "requirements": ["版本", "最低"], "hacs": ["自定义仓库", "下载"], "manual": ["custom_components", "复制"], "setup": ["添加集成", "设备与服务"], "panel": ["侧边栏", "管理员"], "language": ["翻译", "16 种语言"], "removing": ["卸载", "删除", "移除集成"] } }
---
Phoenix MCP 是一个 Home Assistant 集成。安装它，添加一次，它的面板就会出现在侧边栏中。

## 要求 {#requirements}

Home Assistant 2026.7.0 或更高版本。Phoenix 不需要安装其他东西：它的安全层 MESA 随附其中。

## 使用 HACS 安装 {#hacs}

在 HACS 中找到 Phoenix MCP，或把它的仓库添加为集成类型的自定义仓库。下载后重启 Home Assistant。HACS 会替你检查 Home Assistant 版本。

## 手动安装 {#manual}

把 `phoenix_mcp` 文件夹复制到 Home Assistant 的 `custom_components` 文件夹，然后重启 Home Assistant。在较旧的 Home Assistant 上，Phoenix 不会启动，Home Assistant 日志会说明它需要哪个版本。

## 添加集成 {#setup}

在 Home Assistant 中，依次打开设置、设备与服务、添加集成，然后选择 Phoenix MCP。没有需要填写的内容。它只能添加一次。

## 面板 {#panel}

**Phoenix MCP** 会出现在管理员的侧边栏中。其他所有内容都在那里设置，首先是访问密钥。

每个访问密钥在 Home Assistant 中还有一个设备，其传感器显示状态、请求、拒绝、速率限制命中、上次使用和过期时间。智能体看不到这些传感器。

## 语言 {#language}

面板支持 16 种语言。默认情况下它跟随你的 Home Assistant 个人资料；可在[{{label:settings.languageLabel}}](guide:window.settingsGeneral#language)中选择其他语言。此选择仅对当前浏览器有效。指南会以你的语言显示已翻译的页面，其余页面以英文显示。

## 移除 Phoenix MCP {#removing}

删除集成会吊销所有访问密钥、取消等待审批的请求，并清除所有 AI 提供商账户及其 API 密钥。它还会解除 Phoenix 与 Assist、语音智能体、AI 任务和决策提供商的关联。

其他所有内容都保留在 Home Assistant 的存储中。包括紧急停止开关和 MESA 模式等设置，以及审计日志、对话日志、MESA 策略、变更历史和安全副本。如果你再次添加 Phoenix，它会读取这些内容，已吊销的密钥会列在已归档的访问密钥中。

要同时移除 Phoenix 的数据，请在删除集成前使用[{{label:settings.wipeTitle}}](guide:window.settingsSafety#wipe-core)中的 **{{label:settings.wipeCoreLabel}}**。
