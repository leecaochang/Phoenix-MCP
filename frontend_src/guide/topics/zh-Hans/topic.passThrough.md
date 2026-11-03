---
{ "id": "topic.passThrough", "title": "直通模式会开放什么", "parent": "window.accessKeys", "order": 2, "keywords": ["直通模式", "不受限制", "完全访问", "限定范围"] }
---
直通密钥会跳过权限树：它可以读取和控制所有实体，Phoenix 自己的实体除外。请仅对可以放心托付整个家的智能体使用。

## 会开启什么 {#allows}

除非你把它们设为确认，直通模式还会允许以下能力：

- {{label:caps.cap_config_read.label}}、{{label:caps.cap_template_render.label}}、{{label:caps.cap_search.label}} 和 {{label:caps.cap_registry_read.label}}。
- {{label:caps.cap_traces.label}}、{{label:caps.cap_diagnostics.label}}、{{label:caps.cap_broadcast.label}} 和 {{label:caps.cap_service_response.label}}。

{{label:caps.cap_diagnostics.label}} 和 {{label:caps.cap_service_response.label}} 可能暴露系统的敏感细节。

## 什么仍由你控制 {#keeps}

其他所有能力保持各自的设置。包括物理控制、重启、通知、摄像头图像、系统日志、文件、ESPHome YAML 以及所有配置更改。确认仍是确认。

锁、警报控制面板、卷帘和阀门仍然需要 {{label:caps.cap_physical_control.label}}。敏感属性，例如摄像头流地址，仍会被移除。

## 收窄范围 {#limit}

[{{label:workspace.keySettings}}](guide:window.keySettings#assist-exposure)中的 **{{label:accessKeys.assistExposureLabel}}** 会把密钥限制为向 Assist 公开的实体。Home Companion 密钥完全不能使用直通模式。

## 关闭它 {#off}

在[{{label:accessKeys.permissionsTree}}](guide:window.permissions#pass-through)窗口中选择 **{{label:accessKeys.switchToScoped}}**。密钥会再次使用其保存的权限树。
