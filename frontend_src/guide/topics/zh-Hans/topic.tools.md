---
{ "id": "topic.tools", "title": "智能体能做什么", "parent": "topic.about", "order": 30, "keywords": ["工具", "工具列表", "MCP 工具", "智能体能做什么", "tools/list"], "sectionKeywords": { "rules": ["拒绝访问", "隐藏工具", "声明全部工具", "结果大小"], "reading": ["get_state", "get_history", "get_statistics", "get_camera_image", "get_calendar_events", "get_todo_items", "GetLiveContext", "GetDateTime", "历史", "统计", "摄像头"], "controlling": ["HassTurnOn", "HassTurnOff", "HassLightSet", "HassFanSetSpeed", "HassClimateSetTemperature", "HassSetPosition", "HassStopMoving", "HassMediaPause", "HassMediaSearchAndPlay", "HassSetVolume", "HassVacuumStart", "HassCancelAllTimers", "HassBroadcast", "原生工具", "锁", "卷帘", "HassMediaNext", "HassMediaPrevious", "HassMediaUnpause", "HassMediaPlayerMute", "HassMediaPlayerUnmute", "HassSetVolumeRelative", "HassVacuumCleanArea", "HassVacuumReturnToBase"], "actions": ["call_service", "dry_run_service", "服务", "操作", "重新加载", "重启", "响应数据"], "lists": ["set_todo_item", "create_calendar_event", "update_calendar_event", "delete_calendar_event", "get_calendar_write", "待办", "日历"], "waiting": ["watch_entity", "等待状态"], "home-companion": ["home_request", "家庭请求"], "automations": ["list_automations", "get_automation", "create_automation", "edit_automation", "delete_automation", "list_scripts", "get_script", "create_script", "edit_script", "delete_script", "create_blueprint", "edit_blueprint", "delete_blueprint", "蓝图", "间接控制"], "scenes": ["list_scenes", "get_scene", "create_scene", "edit_scene", "delete_scene"], "helpers": ["list_helpers", "create_helper", "edit_helper", "delete_helper", "get_helper_settings", "set_helper_settings", "input_boolean", "计数器", "定时器", "地点", "模板辅助元素"], "dashboards": ["list_dashboards", "create_dashboard", "edit_dashboard", "delete_dashboard", "get_dashboard_config", "set_dashboard_config", "卡片", "仪表盘"], "restore": ["还原", "回执", "回滚"], "mesa": ["MESA 策略", "租约"], "configuration": ["配置", "模板", "日志", "能源"], "diagnostics": ["搜索", "注册表", "跟踪", "诊断"], "files": ["文件", "YAML", "www", "themes"], "esphome": ["ESPHome", "固件"], "system": ["集成", "注册表", "无线电", "备份", "重启", "通知"], "introspection": ["自己的请求", "审计条目"], "household": ["语音", "Assist", "AI 任务", "非管理员"] } }
---
智能体通过工具工作。本页按组说明它们：智能体可以用它们做什么、哪些能力允许它们，以及值得了解的限制。客户端从工具本身读取每个工具的详细信息。

两个设置决定每个操作。[{{label:accessKeys.cardCapabilities}}](guide:window.capabilities)决定智能体可以做哪类事情。[{{label:accessKeys.permissionsTree}}](guide:window.permissions)决定它可以对哪些实体做。

## 能力如何生效 {#rules}

- 能力为拒绝访问的工具不会提供给智能体，对它的调用会在运行前被拒绝。
- **{{label:accessKeys.announceAllLabel}}** 会仍然列出所有工具，用于排查问题。智能体仍然无法使用隐藏的工具。
- 确认会暂缓更改以等待你的审批。使用同一能力的读取会直接运行。
- 列出许多实体的结果会在约 64 KB 时停止。

直通模式允许大多数读取能力，而无需设置它们。每个会更改内容的能力都保留自己的设置。

相关：[拒绝访问、允许和确认](guide:window.capabilities#modes)、[直通模式会开放什么](guide:topic.passThrough)

## 读取家庭信息 {#reading}

智能体可以读取当前状态、历史、统计、日历、待办列表以及整个家庭的摘要。它们只能看到权限树允许它们读取的实体。敏感属性始终会被移除。

大多数读取不需要能力；仅由权限树决定。摄像头静态图像需要一个能力，用搜索过滤器挑选历史或统计实体也需要。

- 历史和统计每页最多 1000 行。
- 一次日历读取最多覆盖 31 天和 500 个事件。
- 摄像头图像每边最多 2048 像素。
- 属性值摘要最多覆盖 7 天。

允许它的能力：[{{label:caps.cap_camera_read.label}}](guide:window.capabilities#camera-read)、[{{label:caps.cap_search.label}}](guide:window.capabilities#search)

## 控制设备 {#controlling}

智能体可以打开和关闭设备，设置灯、风扇、温控、卷帘和音量，控制媒体播放器和吸尘器，并取消定时器。这些命令与 Home Assistant 自带助手使用的命令相同。每个目标都需要权限树中的 W。

锁、警报、卷帘和阀门还需要物理控制。任何会作用到它们的其他操作也需要，例如集成自带的开锁操作；`zwave_js.set_value` 这类原始设备命令无论指向哪个实体都需要。

设为确认时，包含其中一项的命令会等待你的审批。设为拒绝访问时，智能体无法控制它们。

通过 Assist 卫星设备播报消息或在其上发起对话需要广播。MESA 在能力和权限树之后最后检查每个命令。

允许它的能力：[{{label:caps.cap_physical_control.label}}](guide:window.capabilities#physical-control)、[{{label:caps.cap_broadcast.label}}](guide:window.capabilities#broadcast)

相关：[MESA 如何决定](guide:topic.mesaRules)

## 运行任意操作 {#actions}

智能体还可以在它们可以写入的目标上运行 Home Assistant 操作，并先预览结果。目标必须是该操作适用的对象，因此对开关运行 `light.turn_on` 会被拒绝。`homeassistant.turn_on`、`turn_off`、`toggle` 和 `update_entity` 适用于任何对象。少数操作需要更多条件：

- 重启或停止 Home Assistant 需要重启能力。
- 重新加载自动化、脚本、场景、辅助元素或核心配置需要 YAML 配置。
- 操作返回的数据需要服务响应数据。没有它，操作仍会运行。
- 发送消息的通知操作需要通知能力。集成自带的、通过通知实体发送消息的操作也需要。
- 通知播放需要广播能力：`assist_satellite.announce`、`start_conversation`、`ask_question`、`music_assistant.play_announcement`、带 `announce` 的 `media_player.play_media` 和 `tts.speak`。
- `tts.speak` 还需要对其指定的每个音箱有 W。普通播放只需要 W。
- ESPHome 设备操作只需要设备上的 W。MESA 不检查它们，也不检查重新加载或重启。
- 搜索或列出 Music Assistant 音乐库需要服务响应数据，以及某个 Music Assistant 实体（例如音箱）上的 R。这些操作不会播放任何内容，所以 MESA 也不检查它们。

未声明目标的操作（例如 `shell_command`、`rest_command` 或 `python_script`）对每个密钥都会被拒绝，包括直通密钥。智能体指定什么目标都一样，即使是该操作所属域的实体。没有目标的其他操作也会被拒绝。

自定义操作由 Home Assistant 在其实体上运行，或其注册的架构列出 `entity_id` 时，算作已声明目标。

日历更改和 `homeassistant.reload_config_entry` 同样会被拒绝，因为它们有自己的工具。运行超过 30 秒的操作会被报告为可能未完成。

允许它的能力：[{{label:caps.cap_restart.label}}](guide:window.capabilities#restart)、[{{label:caps.cap_yaml_edit.label}}](guide:window.capabilities#yaml-edit)、[{{label:caps.cap_service_response.label}}](guide:window.capabilities#service-response)、[{{label:caps.cap_notify.label}}](guide:window.capabilities#notify)、[{{label:caps.cap_search.label}}](guide:window.capabilities#search)（用于预览）

## 列表和日历 {#lists}

智能体可以添加、更改和移除待办事项，也可以清除已完成的事项，这只需要列表上的 W。移除事项是永久性的：Phoenix 不保留副本，因此[无法还原](guide:topic.configurationHistory#not-recorded)。结果会列出移除了哪些事项。

如果你批准移除，Phoenix 会先重新读取列表。在请求等待期间发生变化的事项会保留，结果会说明是哪些。

智能体可以创建、更改和删除日历事件，这还需要日历写入。MESA 会检查待办和日历更改。每次日历更改都会留下回执，智能体之后可以查看。

允许它的能力：[{{label:caps.cap_calendar_write.label}}](guide:window.capabilities#calendar-write)

## 等待更改 {#waiting}

智能体最多可以等待 30 秒，让实体发生变化或达到某个状态，以确认操作生效。等待结束时，权限树和权限会再次检查。

允许它的能力：[{{label:caps.cap_config_read.label}}](guide:window.capabilities#config-read)

## Home Companion {#home-companion}

[{{label:personas.home_companion.label}}](guide:window.persona#home-companion)密钥只提供一个工具。应用发送一个普通请求，例如“关掉楼下的灯”。Phoenix 用自己的模型执行，检查结果并回报。

- 它控制灯、风扇、开关、温控、卷帘、阀门、媒体播放器、吸尘器、加湿器和定时器。
- 它读取传感器、天气、日历和太阳，但无法更改它们。传感器仅限于温度、湿度、光照、功率、能耗和电池。
- 锁、警报和摄像头不在它的范围内。
- 每个密钥一次处理一个请求，每个请求最多 8 个操作和 2 分钟。
- 直通模式和 **{{label:accessKeys.announceAllLabel}}** 始终关闭。

在 Agent Chat 中它使用聊天的模型。其他所有场合使用语音智能体的提供商和模型。

相关：[语音智能体](guide:topic.homeAssistantFeatures#voice-agent)

## 自动化和脚本 {#automations}

智能体可以读取、创建、编辑和删除 Home Assistant 自带编辑器管理的自动化和脚本。设为确认时，审批会显示新配置，或编辑前后的内容。每次更改都保存在[{{label:shell.tabChanges}}](guide:topic.configurationHistory)中。

自动化以 Home Assistant 身份运行，而不是智能体。其操作不受密钥权限树或 MESA 的限制。因此这些能力让智能体可以间接控制设备。请使用确认来逐个审核。

- 只有编辑器自己的文件会更改。在 YAML 中手工编写的自动化和脚本会被拒绝。
- 列出它们需要读取注册表，并且只显示密钥可以读取其实体的那些。
- 蓝图需要管理蓝图能力，以及蓝图所属类型（自动化或脚本）的能力。

允许它的能力：[{{label:caps.cap_automation_write.label}}](guide:window.capabilities#automation-write)、[{{label:caps.cap_script_write.label}}](guide:window.capabilities#script-write)、[{{label:caps.cap_blueprint_write.label}}](guide:window.capabilities#blueprint-write)

## 场景 {#scenes}

智能体可以读取、创建、编辑和删除 Home Assistant 场景编辑器中的场景。场景中的每个实体都需要权限树中的 W，即使只是读取场景。MESA 不检查场景更改。

允许它的能力：[{{label:caps.cap_scene_write.label}}](guide:window.capabilities#scene-write)

## 辅助元素 {#helpers}

智能体可以创建、编辑和删除辅助元素。包括开关、数字、文本、列表、日期、按钮、计数器、定时器、日程、地点、标签和人员。它们还可以管理通过集成设置的辅助元素，例如模板，并更改其设置。

- MESA 会检查辅助元素更改。MESA 确认会请求自己的审批，即使管理辅助元素为允许。
- 只有地点、标签和人员遵循权限树。其他辅助元素仅受能力和 MESA 限制。
- 删除模板辅助元素还需要集成管理。
- 在 YAML 中定义的辅助元素无法更改。

允许它的能力：[{{label:caps.cap_helper_write.label}}](guide:window.capabilities#helper-write)、[{{label:caps.cap_integration_write.label}}](guide:window.capabilities#integration-write)

## 仪表盘 {#dashboards}

智能体可以列出、创建、编辑和删除仪表盘，读取和搜索其布局，更改卡片并列出已安装的自定义卡片。所有这些都需要管理仪表盘，包括读取。

- 权限树和 MESA 不限制仪表盘更改。密钥无法读取的实体显示为 `<redacted>`，仍包含此类实体的更改会被拒绝。
- 无法显示的布局会被拒绝，对 YAML 模式仪表盘的更改也会被拒绝。
- 删除仪表盘前会先保存其布局，以便还原。
- 自定义卡片列表来自打开过面板的浏览器，最多每 10 分钟刷新一次。

资源，即每个仪表盘加载的 JavaScript 和 CSS，需要仪表盘资源能力。

允许它的能力：[{{label:caps.cap_lovelace_write.label}}](guide:window.capabilities#lovelace-write)、[{{label:caps.cap_dashboard_resources.label}}](guide:window.capabilities#dashboard-resources)

## 还原更改 {#restore}

智能体可以放回自己之前对模板辅助元素、蓝图、能源设置、仪表盘或仪表盘资源所做的更改。它会先请求回执，然后发送还原，还原会经过该能力通常的审批。智能体只能看到自己的记录。

允许它的能力：以下任一项：[{{label:caps.cap_helper_write.label}}](guide:window.capabilities#helper-write)、[{{label:caps.cap_blueprint_write.label}}](guide:window.capabilities#blueprint-write)、[{{label:caps.cap_energy_write.label}}](guide:window.capabilities#energy-write)、[{{label:caps.cap_lovelace_write.label}}](guide:window.capabilities#lovelace-write)、[{{label:caps.cap_dashboard_resources.label}}](guide:window.capabilities#dashboard-resources)

相关：[还原可以放回什么](guide:topic.configurationHistory#restore)

## MESA 策略 {#mesa}

智能体可以读取它们能读取的实体的 MESA 策略，并查看策略为何如此解析。它们还可以在可写入的实体上取得短期租约，最长 30 秒。租约告诉其他人智能体正在那里工作；它不授予任何权限。

允许它的能力：[{{label:caps.cap_config_read.label}}](guide:window.capabilities#config-read)

相关：[MESA 如何决定](guide:topic.mesaRules)

## 配置、模板和日志 {#configuration}

- 读取配置涵盖 Home Assistant 的基本设置、能源设置、太阳能预测和蓝图。
- 管理能源仪表盘会更改能源设置的一部分。它不能为某一类型添加第二个来源，也会拒绝更改已有多个来源的类型。
- 模板渲染会在单独的受限进程中渲染模板。它作用于密钥可读取状态的副本，最长 5 秒。
- 读取系统日志涵盖警告和错误、日志簿和通知。故障日志和捕获的调试记录还需要诊断。
- 集成日志级别会更改集成的日志量，例如开启调试日志。更改可以在 5 到 120 分钟后结束、持续到下次重启，或一直保留到清除。

需要 Home Assistant 本身的模板函数，例如区域或设备查找，不会返回任何内容。

允许它的能力：[{{label:caps.cap_config_read.label}}](guide:window.capabilities#config-read)、[{{label:caps.cap_energy_write.label}}](guide:window.capabilities#energy-write)、[{{label:caps.cap_template_render.label}}](guide:window.capabilities#template-render)、[{{label:caps.cap_log_read.label}}](guide:window.capabilities#log-read)、[{{label:caps.cap_log_control.label}}](guide:window.capabilities#log-control)

## 发现和诊断 {#diagnostics}

这些工具只读取。它们帮助智能体在行动前找到方向。

- 搜索与发现会搜索和描述实体、区域及其关联方式，比较它们，并找出可用的操作。
- 读取注册表会列出区域、楼层、地点、设备、标签、自动化、脚本、场景和辅助元素。
- 自动化跟踪会读取自动化的运行方式。
- 诊断会检查配置并读取系统健康状况、修复项、无线电网络和 ESPHome 概览。

只有在 YAML 配置不为拒绝访问时，配置检查才会指出文件和行号。

允许它的能力：[{{label:caps.cap_search.label}}](guide:window.capabilities#search)、[{{label:caps.cap_registry_read.label}}](guide:window.capabilities#registry-read)、[{{label:caps.cap_traces.label}}](guide:window.capabilities#traces)、[{{label:caps.cap_diagnostics.label}}](guide:window.capabilities#diagnostics)

## 文件和 YAML {#files}

读取文件让智能体读取 www、themes 和 custom_templates 文件夹中的文本文件，每个最大 1 MiB。文件夹列表分页返回，默认每页 500 项，最多 1000 项。它不能写入这些文件。

YAML 配置让智能体读取你的 YAML 文件，名称中含有 secret 的文件、esphome 文件夹和隐藏文件除外。

名称暗示机密的值以及提到机密的注释显示为 `<redacted>`，但使用普通名称的密码可见。有 YAML 错误的文件不会显示，智能体只会得到需要修复的行号。没有工具会写入 YAML 配置。

允许它的能力：[{{label:caps.cap_filesystem.label}}](guide:window.capabilities#filesystem)、[{{label:caps.cap_yaml_edit.label}}](guide:window.capabilities#yaml-edit)

## ESPHome {#esphome}

智能体可以读取和搜索 ESPHome 设备配置，密码、密钥、所有请求头的值、网址中的凭据以及提到它们的注释都会被遮蔽。

使用 ESPHome Device Builder 加载项时，它们还可以校验、编译、读取设备日志、分析内存并检查连接。没有该加载项时，只能读取和搜索。

编译、清理和内存分析需要设备上的 W。安装固件需要 ESPHome 固件刷写、ESPHome 设备 YAML 和设备上的 W，并且 MESA 会检查它。

允许它的能力：[{{label:caps.cap_esphome_yaml.label}}](guide:window.capabilities#esphome-yaml)、[{{label:caps.cap_esphome_flash.label}}](guide:window.capabilities#esphome-flash)

## 集成、设备和系统 {#system}

- 集成管理会添加、启用、禁用、重新加载和移除集成，并移除设备。
- 集成重新配置会更改集成的设置。
- 管理实体注册表会重命名实体和设备，并更改实体 ID、区域、楼层、标签和类别。`recorder.purge_entities` 也需要它，因为该操作会删除历史记录。
- 无线电管理会更改 Zigbee 网络。群组、绑定和设备属性还需要物理控制。MESA 会检查对 Zigbee 设备的更改。
- 没有实体的 Zigbee 设备无从检查，因此建议模式会发出警告并继续。MESA 处于强制模式、启动失败，或你为该设备保存了策略时，则会拒绝。
- 备份会创建和列出备份，但无法还原备份。
- 重启或停止 Home Assistant 会重启它。智能体会被告知不要自行重试。
- 通知会发送通知，无论是通过自己的工具还是运行通知操作。

备份最多等待 45 秒，绝不会传到 Home Assistant Cloud。没有备份加密密钥时它不会加密，并且会包含 secrets.yaml。

允许它的能力：[{{label:caps.cap_integration_write.label}}](guide:window.capabilities#integration-write)、[{{label:caps.cap_integration_reconfigure.label}}](guide:window.capabilities#integration-reconfigure)、[{{label:caps.cap_registry_write.label}}](guide:window.capabilities#registry-write)、[{{label:caps.cap_radio_write.label}}](guide:window.capabilities#radio-write)、[{{label:caps.cap_backup.label}}](guide:window.capabilities#backup)、[{{label:caps.cap_restart.label}}](guide:window.capabilities#restart)、[{{label:caps.cap_notify.label}}](guide:window.capabilities#notify)

## 密钥自身的状态 {#introspection}

每个密钥都可以检查自己的审批请求，最多等待 30 秒以获取其中 50 个，并读取自己的能力摘要和审计条目。这些不需要能力。来自其他密钥的请求看起来就像不存在。

## 在语音、Assist 和 AI 任务中 {#household}

家庭中的任何人都可以对 Assist 或语音智能体说话。对于不是 Home Assistant 管理员的人，密钥最多保留五项能力。它们是搜索与发现、读取注册表、物理控制、广播和服务响应数据。其余都算作拒绝访问。

审批和审计工具也会被隐藏，Home Assistant 仅限管理员的操作会被拒绝。Home Assistant 无法识别的说话者也会被同样对待。AI 任务始终如此运行。

相关：[语音智能体、AI 任务或 Assist 工具提供商](guide:topic.homeAssistantFeatures)、[Assist 和语音智能体](guide:topic.permissionResolution#household)
