---
{ "id": "window.capabilities", "title": "{{label:accessKeys.cardCapabilities}}", "parent": "workspace.accessKeys", "order": 3, "keywords": ["工具", "权限", "审批", "拒绝访问", "允许", "确认"], "sectionKeywords": { "modes": ["审批", "队列"], "physical-control": ["锁", "警报", "卷帘", "阀门", "车库门"], "restart": ["重启"], "backup": ["快照"], "camera-read": ["图像", "快照"], "filesystem": ["www", "themes", "文件"], "lovelace-write": ["Lovelace", "仪表盘"], "dashboard-resources": ["自定义卡片", "JavaScript"], "yaml-edit": ["configuration.yaml", "重新加载"], "esphome-flash": ["固件", "OTA"], "registry-write": ["重命名实体", "实体 ID"], "radio-write": ["Zigbee", "ZHA"], "log-control": ["调试日志"] } }
---
能力决定密钥可以使用哪些工具。每项能力可设为{{label:caps.modeDeny}}、{{label:caps.modeAllow}}或{{label:caps.modeConfirm}}。新密钥的每项能力都从{{label:caps.modeDeny}}开始。

能力决定智能体可以做哪类事情。[{{label:accessKeys.permissionsTree}}](guide:window.permissions)决定它可以对哪些设备和实体做。

## 拒绝访问、允许和确认 {#modes}

- **{{label:caps.modeDeny}}**：智能体不能使用这些工具。它们不会提供给智能体，除非开启了{{label:accessKeys.announceAllLabel}}。
- **{{label:caps.modeAllow}}**：智能体可以自由使用它们。
- **{{label:caps.modeConfirm}}**：更改会在[审批](guide:workspace.approvals)中等待你决定。使用同一能力的读取会直接运行。

{{label:caps.tierRead}}和{{label:caps.tierEveryday}}只能设为{{label:caps.modeDeny}}或{{label:caps.modeAllow}}。它们的 **{{label:caps.modeConfirm}}** 按钮为灰色。

更改会立即生效。放宽能力会让智能体继续。收紧能力会拒绝智能体已在进行的请求。你批准请求时，Phoenix 会再次检查能力。如果现在为{{label:caps.modeDeny}}，请求会失败。

相关：[同步等待审批](guide:window.keySettings#inline-wait)、[直通模式会开放什么](guide:topic.passThrough)

## {{label:caps.tierRead}} {#reads}

### {{label:caps.cap_config_read.label}} {#config-read}

读取 Home Assistant 的配置、能源设置、太阳能预测、蓝图和 MESA 策略。直通模式允许它。

{{tools:cap_config_read}}

### {{label:caps.cap_camera_read.label}} {#camera-read}

从密钥可访问的摄像头获取静态图像。以媒体源指定摄像头、图像或摄像头录像的操作（例如 AI 任务附件）也需要它。指定私人照片、文件或截图库的操作同样需要。没有角色允许它，直通模式也不允许。

{{tools:cap_camera_read}}

### {{label:caps.cap_template_render.label}} {#template-render}

渲染 Home Assistant 模板。直通模式允许它。

{{tools:cap_template_render}}

### {{label:caps.cap_log_read.label}} {#log-read}

读取系统日志、日志簿、通知和集成日志级别。直通模式不允许它。

{{tools:cap_log_read}}

### {{label:caps.cap_search.label}} {#search}

搜索实体，并描述区域、实体、关系和可用操作，包括试运行。每个角色和直通模式都允许它。

{{tools:cap_search}}

### {{label:caps.cap_registry_read.label}} {#registry-read}

列出区域、楼层、地点、设备、自动化、脚本、场景、辅助元素和标签。每个角色和直通模式都允许它。

{{tools:cap_registry_read}}

### {{label:caps.cap_traces.label}} {#traces}

读取自动化跟踪。直通模式允许它。

{{tools:cap_traces}}

### {{label:caps.cap_diagnostics.label}} {#diagnostics}

读取系统健康状况、修复项、配置检查、无线电网络和 ESPHome 概览，并校验自动化和脚本。配合 {{label:caps.cap_log_read.label}}，它还会读取故障日志、捕获的调试记录和 Phoenix 自己的诊断。直通模式允许它。

{{tools:cap_diagnostics}}

### {{label:caps.cap_filesystem.label}} {#filesystem}

读取 www、themes 和 custom_templates 文件夹中的文件。直通模式不允许它。

{{tools:cap_filesystem}}

## {{label:caps.tierEveryday}} {#everyday}

### {{label:caps.cap_broadcast.label}} {#broadcast}

通过 Assist 卫星设备播报消息、在其上发起对话，并在音箱上播放通知和语音文本。密钥还需要对音箱有 W。普通播放只需要 W。直通模式允许它。

{{tools:cap_broadcast}}

### {{label:caps.cap_service_response.label}} {#service-response}

返回操作响应的数据。没有它，大多数操作仍会运行，但不返回数据。Phoenix 会拒绝只返回数据的操作，例如天气预报和 Music Assistant 音乐库搜索。直通模式允许它。

{{tools:cap_service_response}}

### {{label:caps.cap_esphome_yaml.label}} {#esphome-yaml}

读取和搜索 ESPHome 设备配置。配合 ESPHome Device Builder，它还可以校验、编译和检查设备。直通模式不允许它。

{{tools:cap_esphome_yaml}}

## {{label:caps.tierConfigWrite}} {#config-changes}

直通模式从不更改这些能力；每项都保留自己的设置。

### {{label:caps.cap_automation_write.label}} {#automation-write}

读取、创建、编辑和删除自动化。添加运行代码或触及主机的调用时，始终需要您审批，即使设为{{label:caps.modeAllow}}也一样。重启、更新、模板化的动作名称和蓝图也是如此。

{{tools:cap_automation_write}}

### {{label:caps.cap_script_write.label}} {#script-write}

读取、创建、编辑和删除脚本。自动化的审批规则同样适用于此。Phoenix 会拒绝蓝图中除 `!input` 以外的 YAML 标签，例如 `!include` 或 `!env_var`，因为它们会读取主机上的文件和环境变量。

{{tools:cap_script_write}}

### {{label:caps.cap_blueprint_write.label}} {#blueprint-write}

创建、编辑和删除蓝图。写入蓝图还需要匹配的自动化或脚本能力：任一为{{label:caps.modeDeny}}，Phoenix 就会拒绝它。任一为{{label:caps.modeConfirm}}，写入就会等待你的审批。自动化的审批规则同样适用于此。

{{tools:cap_blueprint_write}}

### {{label:caps.cap_scene_write.label}} {#scene-write}

读取、创建、编辑和删除场景。密钥需要对场景中的每个实体有 W。

{{tools:cap_scene_write}}

### {{label:caps.cap_helper_write.label}} {#helper-write}

创建、编辑和删除辅助元素，并更改其设置。

{{tools:cap_helper_write}}

### {{label:caps.cap_calendar_write.label}} {#calendar-write}

创建、更改和删除日历事件。

{{tools:cap_calendar_write}}

## {{label:caps.tierSystem}} {#system}

### {{label:caps.cap_physical_control.label}} {#physical-control}

控制锁、警报控制面板、卷帘和阀门，无论通过它们自身的操作，还是其他会作用到它们的操作。`zwave_js.set_value` 这类原始设备命令始终需要它，设备上有这些实体时，其 ESPHome 操作也需要。

智能体还需要在{{label:accessKeys.permissionsTree}}中对它们有 W。原始命令或 ESPHome 操作需要对其设备能操作的一切拥有 W，包括按钮。更改 Zigbee 设备的无线电工具也需要{{label:caps.cap_physical_control.label}}。

创建或编辑包含这些实体的场景也需要此能力。

{{tools:cap_physical_control}}

### {{label:caps.cap_notify.label}} {#notify}

通过自己的工具、通知操作或其他通过通知实体发送消息的操作发送通知。手机以外的旧版通知操作始终需要您审批，即使设为{{label:caps.modeAllow}}也一样。

{{tools:cap_notify}}

### {{label:caps.cap_restart.label}} {#restart}

重启或停止 Home Assistant 并安装更新。每次更新都需要您审批，即使设为{{label:caps.modeAllow}}也一样。ESPHome 固件使用它自己的能力。

{{tools:cap_restart}}

### {{label:caps.cap_log_control.label}} {#log-control}

更改集成的日志级别。它还需要 {{label:caps.cap_log_read.label}}。没有角色允许它。

{{tools:cap_log_control}}

### {{label:caps.cap_integration_write.label}} {#integration-write}

添加、更改、启用、禁用、重新加载和移除集成，并移除设备。

{{tools:cap_integration_write}}

### {{label:caps.cap_integration_reconfigure.label}} {#integration-reconfigure}

重新配置集成，添加或更改其子条目（例如 AI 代理），并修复其修复项。子条目和修复始终需要你的批准，即使设为{{label:caps.modeAllow}}也是如此。

{{tools:cap_integration_reconfigure}}

### {{label:caps.cap_lovelace_write.label}} {#lovelace-write}

读取和更改仪表盘。没有它，智能体也无法读取仪表盘。

卡片会以打开仪表盘的人的权限运行。因此，添加模板、添加或更改任何自定义卡片，或添加来自其他网站的图片或链接的更改，即使设为{{label:caps.modeAllow}}也会等待你的批准。智能体还原早先的布局并带回此类卡片时也是如此。

把仅限管理员的仪表盘设为所有人可见，也会等待你的批准。

仪表盘上已有且未更改的自定义卡片不算在内。不含以上内容的内置卡片遵循你设置的模式。

Phoenix 会先试运行仪表盘更改中的新模板。如果某个模板崩溃、耗时过长或超出限制，Phoenix 会拒绝该更改。

{{tools:cap_lovelace_write}}

### {{label:caps.cap_registry_write.label}} {#registry-write}

重命名实体，更改其区域、设备、标签和类别，并删除它们。实体的 ID 可以在其域内更改。使用 `recorder.purge_entities` 清除实体的历史记录也需要此能力。

{{tools:cap_registry_write}}

### {{label:caps.cap_radio_write.label}} {#radio-write}

管理 Zigbee 网络。扫描网络还需要 {{label:caps.cap_diagnostics.label}}；更改设备、绑定和群组还需要 {{label:caps.cap_physical_control.label}}。

{{tools:cap_radio_write}}

### {{label:caps.cap_energy_write.label}} {#energy-write}

更改能源仪表盘的设置。

{{tools:cap_energy_write}}

### {{label:caps.cap_esphome_flash.label}} {#esphome-flash}

通过 ESPHome Device Builder 在 ESPHome 设备上安装固件。它还需要 {{label:caps.cap_esphome_yaml.label}}。只有{{label:personas.esphome.label}}角色允许它，并且需要你的审批。ESPHome 设备上的 `update.install` 也需要此能力。

{{tools:cap_esphome_flash}}

### {{label:caps.cap_yaml_edit.label}} {#yaml-edit}

读取 Home Assistant 的 YAML 配置文件并运行其重新加载操作。它还会为配置检查添加文件和行号。

{{tools:cap_yaml_edit}}

## {{label:caps.tierIrreversible}} {#high-risk}

### {{label:caps.cap_backup.label}} {#backup}

列出和创建备份。

{{tools:cap_backup}}

### {{label:caps.cap_dashboard_resources.label}} {#dashboard-resources}

添加和更改仪表盘资源，即自定义卡片加载的 JavaScript 文件。每次更改都需要您审批，因此没有{{label:caps.modeAllow}}选项。文件必须来自 `/local/` 或 `/hacsfiles/`。Phoenix 只更改它自己添加的资源。

每个打开仪表盘的浏览器都会以该用户的 Home Assistant 访问权限运行这些文件。管理员的浏览器打开 Phoenix MCP 面板时也会运行它们，以读取它们添加的自定义卡片。

{{tools:cap_dashboard_resources}}

## 无需能力的工具 {#no-capability}

[{{label:accessKeys.permissionsTree}}](guide:window.permissions)决定它们能触及哪些实体。会做出更改的工具，只有在权限树给密钥某处“写入”权限时才会提供。

有些调用仍需要一项能力，例如锁门需要{{label:caps.cap_physical_control.label}}。

{{tools:none}}
