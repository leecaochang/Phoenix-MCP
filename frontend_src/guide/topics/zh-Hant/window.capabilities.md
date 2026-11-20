---
{ "id": "window.capabilities", "title": "{{label:accessKeys.cardCapabilities}}", "parent": "workspace.accessKeys", "order": 3, "keywords": ["工具", "權限", "核准", "拒絕存取", "允許", "確認"], "sectionKeywords": { "modes": ["核准", "佇列"], "physical-control": ["門鎖", "警報", "捲簾", "閥門", "車庫門"], "restart": ["重新啟動"], "backup": ["快照"], "camera-read": ["影像", "快照"], "filesystem": ["www", "themes", "檔案"], "lovelace-write": ["Lovelace", "儀表板"], "dashboard-resources": ["自訂卡片", "JavaScript"], "yaml-edit": ["configuration.yaml", "重新載入"], "esphome-flash": ["韌體", "OTA"], "registry-write": ["重新命名實體", "實體 ID"], "radio-write": ["Zigbee", "ZHA"], "log-control": ["偵錯日誌"] } }
---
能力決定金鑰可以使用哪些工具。每項能力可設為{{label:caps.modeDeny}}、{{label:caps.modeAllow}}或{{label:caps.modeConfirm}}。新金鑰的每項能力都從{{label:caps.modeDeny}}開始。

能力決定智慧體可以做哪類事情。[{{label:accessKeys.permissionsTree}}](guide:window.permissions)決定它可以對哪些裝置和實體做。

## 拒絕存取、允許和確認 {#modes}

- **{{label:caps.modeDeny}}**：智慧體不能使用這些工具。它們不會提供給智慧體，除非開啟了{{label:accessKeys.announceAllLabel}}。
- **{{label:caps.modeAllow}}**：智慧體可以自由使用它們。
- **{{label:caps.modeConfirm}}**：變更會在[核准](guide:workspace.approvals)中等待你決定。使用同一能力的讀取會直接執行。

{{label:caps.tierRead}}和{{label:caps.tierEveryday}}只能設為{{label:caps.modeDeny}}或{{label:caps.modeAllow}}。它們的 **{{label:caps.modeConfirm}}** 按鈕會顯示為灰色。

變更會立即生效。放寬能力會讓智慧體繼續。收緊能力會拒絕智慧體已在進行的請求。你核准請求時，Phoenix 會再次檢查能力。如果現在為{{label:caps.modeDeny}}，請求會失敗。

相關：[同步等待核准](guide:window.keySettings#inline-wait)、[直通模式會開放什麼](guide:topic.passThrough)

## {{label:caps.tierRead}} {#reads}

### {{label:caps.cap_config_read.label}} {#config-read}

讀取 Home Assistant 的設定、能源設定、太陽能預測、藍圖和 MESA 策略。直通模式允許它。

{{tools:cap_config_read}}

### {{label:caps.cap_camera_read.label}} {#camera-read}

從金鑰可存取的攝影機取得靜態影像。以媒體來源指定攝影機、影像或攝影機錄影的操作（例如 AI 任務附件）也需要它。指定私人相片、檔案或擷取畫面庫的操作同樣需要。沒有角色允許它，直通模式也不允許。

{{tools:cap_camera_read}}

### {{label:caps.cap_template_render.label}} {#template-render}

渲染 Home Assistant 模板。直通模式允許它。

{{tools:cap_template_render}}

### {{label:caps.cap_log_read.label}} {#log-read}

讀取系統日誌、日誌簿、通知和整合日誌級別。直通模式不允許它。

{{tools:cap_log_read}}

### {{label:caps.cap_search.label}} {#search}

搜尋實體，並描述分區、實體、關係和可用操作，包括試運作。每個角色和直通模式都允許它。

{{tools:cap_search}}

### {{label:caps.cap_registry_read.label}} {#registry-read}

列出分區、樓層、區域、裝置、自動化、腳本、場景、輔助項目和分類。每個角色和直通模式都允許它。

{{tools:cap_registry_read}}

### {{label:caps.cap_traces.label}} {#traces}

讀取自動化追蹤。直通模式允許它。

{{tools:cap_traces}}

### {{label:caps.cap_diagnostics.label}} {#diagnostics}

讀取系統健康狀況、修復項、設定檢查、無線網路和 ESPHome 概覽，並校驗自動化和腳本。搭配 {{label:caps.cap_log_read.label}}，它還會讀取故障日誌、擷取的偵錯記錄和 Phoenix 自己的診斷。直通模式允許它。

{{tools:cap_diagnostics}}

### {{label:caps.cap_filesystem.label}} {#filesystem}

讀取 www、themes 和 custom_templates 資料夾中的檔案。直通模式不允許它。

{{tools:cap_filesystem}}

## {{label:caps.tierEveryday}} {#everyday}

### {{label:caps.cap_broadcast.label}} {#broadcast}

透過 Assist 衛星裝置廣播訊息、在其上發起對話，並在喇叭上播放公告和語音文字。金鑰還需要對喇叭有 W。一般播放只需要 W。直通模式允許它。

{{tools:cap_broadcast}}

### {{label:caps.cap_service_response.label}} {#service-response}

傳回操作回應的資料。沒有它，大多數操作仍會執行，但不傳回資料。Phoenix 會拒絕只傳回資料的操作，例如天氣預報和 Music Assistant 音樂庫搜尋。直通模式允許它。

{{tools:cap_service_response}}

### {{label:caps.cap_esphome_yaml.label}} {#esphome-yaml}

讀取和搜尋 ESPHome 裝置設定。搭配 ESPHome Device Builder，它還可以校驗、編譯和檢查裝置。直通模式不允許它。

{{tools:cap_esphome_yaml}}

## {{label:caps.tierConfigWrite}} {#config-changes}

直通模式從不變更這些能力；每項都保留自己的設定。

### {{label:caps.cap_automation_write.label}} {#automation-write}

讀取、建立、編輯和刪除自動化。新增執行程式碼或觸及主機的呼叫時，一律需要您核准，即使設為{{label:caps.modeAllow}}也一樣。重新啟動、更新、範本化的動作名稱和藍圖也是如此。

{{tools:cap_automation_write}}

### {{label:caps.cap_script_write.label}} {#script-write}

讀取、建立、編輯和刪除腳本。自動化的核准規則同樣適用於此。Phoenix 會拒絕藍圖中除 `!input` 以外的 YAML 標籤，例如 `!include` 或 `!env_var`，因為它們會讀取主機上的檔案和環境變數。

{{tools:cap_script_write}}

### {{label:caps.cap_blueprint_write.label}} {#blueprint-write}

建立、編輯和刪除藍圖。寫入藍圖還需要符合的自動化或腳本能力：任一為{{label:caps.modeDeny}}，Phoenix 就會拒絕它。任一為{{label:caps.modeConfirm}}，寫入就會等待你的核准。自動化的核准規則同樣適用於此。

{{tools:cap_blueprint_write}}

### {{label:caps.cap_scene_write.label}} {#scene-write}

讀取、建立、編輯和刪除場景。金鑰需要對場景中的每個實體有 W。

{{tools:cap_scene_write}}

### {{label:caps.cap_helper_write.label}} {#helper-write}

建立、編輯和刪除輔助項目，並變更其設定。

{{tools:cap_helper_write}}

### {{label:caps.cap_calendar_write.label}} {#calendar-write}

建立、變更和刪除行事曆事件。

{{tools:cap_calendar_write}}

## {{label:caps.tierSystem}} {#system}

### {{label:caps.cap_physical_control.label}} {#physical-control}

控制門鎖、警報控制面板、捲簾和閥門，無論透過它們自身的操作，還是其他會作用到它們的操作。`zwave_js.set_value` 這類原始裝置命令始終需要它，裝置上有這些實體時，其 ESPHome 操作也需要。

智慧體還需要在{{label:accessKeys.permissionsTree}}中對它們有 W。原始命令或 ESPHome 操作需要對其裝置能操作的一切擁有 W，包括按鈕。變更 Zigbee 裝置的無線電工具也需要{{label:caps.cap_physical_control.label}}。

建立或編輯包含這些實體的場景也需要此能力。

{{tools:cap_physical_control}}

### {{label:caps.cap_notify.label}} {#notify}

透過自己的工具、通知操作或其他透過通知實體傳送訊息的操作傳送通知。手機以外的舊版通知操作一律需要您核准，即使設為{{label:caps.modeAllow}}也一樣。

{{tools:cap_notify}}

### {{label:caps.cap_restart.label}} {#restart}

重新啟動或停止 Home Assistant 並安裝更新。每次更新都需要您核准，即使設為{{label:caps.modeAllow}}也一樣。ESPHome 韌體使用它自己的能力。

{{tools:cap_restart}}

### {{label:caps.cap_log_control.label}} {#log-control}

變更整合的日誌級別。它還需要 {{label:caps.cap_log_read.label}}。沒有角色允許它。

{{tools:cap_log_control}}

### {{label:caps.cap_integration_write.label}} {#integration-write}

新增、變更、啟用、停用、重新載入和移除整合，並移除裝置。

{{tools:cap_integration_write}}

### {{label:caps.cap_integration_reconfigure.label}} {#integration-reconfigure}

重新設定整合，新增或變更其子項目（例如 AI 代理），並修復其修復項。子項目和修復始終需要你的核准，即使設為{{label:caps.modeAllow}}也是如此。

{{tools:cap_integration_reconfigure}}

### {{label:caps.cap_lovelace_write.label}} {#lovelace-write}

讀取和變更儀表板。沒有它，智慧體也無法讀取儀表板。

卡片會以開啟儀表板的人的權限執行。因此，新增範本、新增或變更任何自訂卡片，或新增來自其他網站的圖片或連結的變更，即使設為{{label:caps.modeAllow}}也會等待你的核准。智慧體還原先前的佈局並帶回此類卡片時也是如此。

把僅限管理員的儀表板設為所有人可見，也會等待你的核准。

儀表板上已有且未變更的自訂卡片不算在內。不含以上內容的內建卡片遵循你設定的模式。

Phoenix 會先試執行儀表板變更中的新範本。如果某個範本當機、耗時過長或超出限制，Phoenix 會拒絕該變更。

{{tools:cap_lovelace_write}}

### {{label:caps.cap_registry_write.label}} {#registry-write}

重新命名實體，變更其分區、裝置、分類和類別，並刪除它們。實體的 ID 可以在其域內變更。使用 `recorder.purge_entities` 清除實體的歷史記錄也需要此能力。

{{tools:cap_registry_write}}

### {{label:caps.cap_radio_write.label}} {#radio-write}

管理 Zigbee 網路。掃描網路還需要 {{label:caps.cap_diagnostics.label}}；變更裝置、綁定和群組還需要 {{label:caps.cap_physical_control.label}}。

{{tools:cap_radio_write}}

### {{label:caps.cap_energy_write.label}} {#energy-write}

變更能源儀表板的設定。

{{tools:cap_energy_write}}

### {{label:caps.cap_esphome_flash.label}} {#esphome-flash}

透過 ESPHome Device Builder 在 ESPHome 裝置上安裝韌體。它還需要 {{label:caps.cap_esphome_yaml.label}}。只有{{label:personas.esphome.label}}角色允許它，並且需要你的核准。ESPHome 裝置上的 `update.install` 也需要此能力。

{{tools:cap_esphome_flash}}

### {{label:caps.cap_yaml_edit.label}} {#yaml-edit}

讀取 Home Assistant 的 YAML 設定檔並執行其重新載入操作。它還會為設定檢查新增檔案和行號。

{{tools:cap_yaml_edit}}

## {{label:caps.tierIrreversible}} {#high-risk}

### {{label:caps.cap_backup.label}} {#backup}

列出和建立備份。

{{tools:cap_backup}}

### {{label:caps.cap_dashboard_resources.label}} {#dashboard-resources}

新增和變更儀表板資源，即自訂卡片載入的 JavaScript 檔案。每次變更都需要您核准，因此沒有{{label:caps.modeAllow}}選項。檔案必須來自 `/local/` 或 `/hacsfiles/`。Phoenix 只變更它自己新增的資源。

每個開啟儀表板的瀏覽器都會以該使用者的 Home Assistant 存取權限執行這些檔案。管理員的瀏覽器開啟 Phoenix MCP 面板時也會執行它們，以讀取它們新增的自訂卡片。

{{tools:cap_dashboard_resources}}

## 不需要能力的工具 {#no-capability}

[{{label:accessKeys.permissionsTree}}](guide:window.permissions)決定它們能觸及哪些實體。會做出變更的工具，只有在權限樹給金鑰某處「寫入」權限時才會提供。

有些呼叫仍需要一項能力，例如鎖門需要{{label:caps.cap_physical_control.label}}。

{{tools:none}}
