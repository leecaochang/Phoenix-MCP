---
{ "id": "window.persona", "title": "{{label:accessKeys.cardPersona}}", "parent": "workspace.accessKeys", "order": 2, "keywords": ["角色", "预设能力", "模板"], "sectionKeywords": { "home-companion": ["家庭", "家人"], "esphome": ["固件"], "maintenance": ["备份"] } }
---
角色会一次性设定密钥的所有能力，以适应某类智能体。选择一个角色，然后如有需要，在[{{label:accessKeys.cardCapabilities}}](guide:window.capabilities)中调整单项能力。你的选择会立即生效。

角色决定智能体可以使用哪些工具，而不是它可以访问哪些设备。后者由[{{label:accessKeys.permissionsTree}}](guide:window.permissions)决定。唯一的例外是 Home Companion。

角色不会阻止智能体控制设备。除 Home Companion 外，使用任何角色时，智能体都可以控制你在权限树中设为 W 的任何内容。只有锁、警报控制面板、卷帘和阀门还需要[{{label:caps.cap_physical_control.label}}](guide:window.capabilities#physical-control)。

除 {{label:settings.colorTheme.spartan}} 外的任何[{{label:settings.colorThemeLabel}}](guide:window.settingsGeneral#color-theme)下，三个角色有自己的颜色：{{label:personas.home_companion.label}}为金色，{{label:personas.new_user.label}}为绿色，{{label:personas.custom.label}}为蓝色。

## {{label:personas.read_only.label}} {#read-only}

只读取：配置、模板、系统日志、搜索、注册表、自动化跟踪、诊断和服务响应数据。它不更改任何配置，但仍可控制权限树中设为 W 的任何对象。

## {{label:personas.voice_assistant.label}} {#voice-assistant}

读取配置、模板、搜索和注册表，获取服务响应数据，并可以广播。通知和物理控制需要你的审批。

## {{label:personas.dashboard_designer.label}} {#dashboard-designer}

读取配置、模板、搜索、注册表以及主题和卡片文件，并管理仪表盘。对能源仪表盘和仪表盘资源的更改需要你的审批。

## {{label:personas.maintenance.label}} {#maintenance}

读取配置、模板、日志、搜索、注册表、跟踪和诊断，并创建备份。重启、实体注册表更改和无线电管理需要你的审批。

## {{label:personas.automation_builder.label}} {#automation-builder}

包含所有常规读取，并管理自动化、脚本、蓝图、场景、辅助元素和日历事件。它还可以广播。通知、物理控制和重启需要你的审批。

## {{label:personas.power_user.label}} {#power-user}

包含自动化构建器的所有内容，并可无需询问地重启。集成、仪表盘、实体注册表、无线电、能源仪表盘、仪表盘资源和备份需要你的审批。

## {{label:personas.home_admin.label}} {#home-admin}

包含高级用户的所有内容，并读取主题和卡片文件以及 ESPHome 设备 YAML。重启和 YAML 配置也需要你的审批。它不允许摄像头图像、集成日志级别或 ESPHome 固件刷写。

## {{label:personas.new_user.label}} {#new-user}

读取配置、模板、搜索和注册表，并获取服务响应数据。物理控制需要你的审批。设置向导从这个角色开始。

## {{label:personas.esphome.label}} {#esphome}

读取配置、日志、搜索、注册表、诊断和 ESPHome 设备 YAML。刷写固件需要你的审批；这是唯一允许刷写的角色。

## {{label:personas.home_companion.label}} {#home-companion}

用于家庭助手。智能体只获得一个工具，把请求转交给 Phoenix 自己的模型，并且无论权限树授予什么，都只能访问家用设备。物理控制需要你的审批。

它可以控制灯、风扇、开关、开关辅助元素、温控、遮盖物、阀门、媒体播放器、扫地机、加湿器和计时器。

它可以读取二元传感器、天气、日历和太阳，以及温度、湿度、光照、功率、电能和电池传感器。即使设为 W，它也不能更改这些。其他内容它都无法访问，因此权限树在那里显示无访问权限。

在 Agent Chat 中它使用那里选择的账户；其他所有场合使用语音智能体的账户和模型。它不能使用直通模式或声明全部工具：选择它会将两者关闭，并在关闭时告知你。更改能力不会使它变为自定义。

## {{label:personas.custom.label}} {#custom}

能力的任意组合。更改单项能力后，角色会变为自定义，或变为设置现在匹配的角色。

如果切换离开时角色是自定义，且你尚未重新加载页面，再次选择自定义会恢复密钥之前的自定义设置。
