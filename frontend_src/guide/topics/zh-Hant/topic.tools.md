---
{ "id": "topic.tools", "title": "智慧體可以做什麼", "parent": "topic.about", "order": 30, "keywords": ["工具", "工具清單", "MCP 工具", "智慧體可以做什麼", "tools/list"], "sectionKeywords": { "rules": ["拒絕存取", "隱藏工具", "宣告全部工具", "結果大小"], "reading": ["get_state", "get_history", "get_statistics", "get_camera_image", "get_calendar_events", "get_todo_items", "GetLiveContext", "GetDateTime", "歷史", "統計", "攝影機"], "controlling": ["HassTurnOn", "HassTurnOff", "HassLightSet", "HassFanSetSpeed", "HassClimateSetTemperature", "HassSetPosition", "HassStopMoving", "HassMediaPause", "HassMediaSearchAndPlay", "HassSetVolume", "HassVacuumStart", "HassCancelAllTimers", "HassBroadcast", "原生工具", "門鎖", "捲簾", "HassMediaNext", "HassMediaPrevious", "HassMediaUnpause", "HassMediaPlayerMute", "HassMediaPlayerUnmute", "HassSetVolumeRelative", "HassVacuumCleanArea", "HassVacuumReturnToBase"], "actions": ["call_service", "dry_run_service", "服務", "操作", "重新載入", "重新啟動", "回應資料"], "lists": ["set_todo_item", "create_calendar_event", "update_calendar_event", "delete_calendar_event", "get_calendar_write", "待辦", "行事曆"], "waiting": ["watch_entity", "等待狀態"], "home-companion": ["home_request", "家庭請求"], "automations": ["list_automations", "get_automation", "create_automation", "edit_automation", "delete_automation", "list_scripts", "get_script", "create_script", "edit_script", "delete_script", "create_blueprint", "edit_blueprint", "delete_blueprint", "藍圖", "間接控制"], "scenes": ["list_scenes", "get_scene", "create_scene", "edit_scene", "delete_scene"], "helpers": ["list_helpers", "create_helper", "edit_helper", "delete_helper", "get_helper_settings", "set_helper_settings", "input_boolean", "計數器", "計時器", "區域", "模板輔助項目"], "dashboards": ["list_dashboards", "create_dashboard", "edit_dashboard", "delete_dashboard", "get_dashboard_config", "set_dashboard_config", "卡片", "儀表板"], "restore": ["還原", "回執", "回滾"], "mesa": ["MESA 策略", "租約"], "configuration": ["設定", "模板", "日誌", "能源"], "diagnostics": ["搜尋", "登錄檔", "追蹤", "診斷"], "files": ["檔案", "YAML", "www", "themes"], "esphome": ["ESPHome", "韌體"], "system": ["整合", "登錄檔", "無線電", "備份", "重新啟動", "通知"], "introspection": ["自己的請求", "稽核條目"], "household": ["語音", "Assist", "AI 任務", "非管理員"] } }
---
智慧體透過工具工作。本頁依群組說明它們：智慧體可以用它們做什麼、哪些能力允許它們，以及值得了解的限制。用戶端會從工具本身讀取每個工具的詳細資料。

兩個設定決定每個操作。[{{label:accessKeys.cardCapabilities}}](guide:window.capabilities)決定智慧體可以做哪類事情。[{{label:accessKeys.permissionsTree}}](guide:window.permissions)決定它可以對哪些實體做。

## 能力如何生效 {#rules}

- 能力為拒絕存取的工具不會提供給智慧體，對它的呼叫會在執行前被拒絕。
- **{{label:accessKeys.announceAllLabel}}** 仍會列出所有工具，用於疑難排解。智慧體仍然無法使用隱藏的工具。
- 確認會暫緩變更以等待你的核准。使用同一能力的讀取會直接執行。
- 列出許多實體的結果會在約 64 KB 時停止。

直通模式允許大多數讀取能力，而無需設定它們。每個會變更內容的能力都保留自己的設定。

相關：[拒絕存取、允許和確認](guide:window.capabilities#modes)、[直通模式會開放什麼](guide:topic.passThrough)

## 讀取家庭資訊 {#reading}

智慧體可以讀取目前狀態、歷史、統計、行事曆、待辦清單以及整個家庭的摘要。它們只能看到權限樹允許它們讀取的實體。敏感屬性一律會被移除。

大多數讀取不需要能力；僅由權限樹決定。攝影機靜態影像需要一個能力，用搜尋篩選器挑選歷史或統計實體也需要。

- 歷史和統計每頁最多 1000 列。
- 一次行事曆讀取最多涵蓋 31 天和 500 個事件。
- 攝影機影像每邊最多 2048 像素。
- 屬性值摘要最多涵蓋 7 天。

允許它的能力：[{{label:caps.cap_camera_read.label}}](guide:window.capabilities#camera-read)、[{{label:caps.cap_search.label}}](guide:window.capabilities#search)

## 控制裝置 {#controlling}

智慧體可以開啟和關閉裝置，設定燈、風扇、溫控、捲簾和音量，控制媒體播放器和吸塵器，並取消計時器。這些命令與 Home Assistant 自帶助理使用的命令相同。每個目標都需要權限樹中的 W。

門鎖、警報、捲簾和閥門還需要物理控制。任何會作用到它們的其他操作也需要，例如整合自帶的開鎖操作；`zwave_js.set_value` 這類原始裝置命令無論指向哪個實體都需要。

設為確認時，包含其中一項的命令會等待你的核准。設為拒絕存取時，智慧體無法控制它們。

原始 Z-Wave 或 ISY 命令作用於整個裝置。智慧體需要對裝置能操作的一切（例如門鎖、燈和開關）有 W，MESA 也會檢查裝置的每個實體。

開啟 `broadcast` 的 Z-Wave 多點傳送會被拒絕，因為它會到達網路中的每個裝置。

透過 Assist 衛星裝置廣播訊息或在其上發起對話需要廣播。MESA 會在能力和權限樹之後最後檢查每個命令。

允許它的能力：[{{label:caps.cap_physical_control.label}}](guide:window.capabilities#physical-control)、[{{label:caps.cap_broadcast.label}}](guide:window.capabilities#broadcast)

相關：[MESA 如何決定](guide:topic.mesaRules)

## 執行任意操作 {#actions}

智慧體還可以在它們可以寫入的目標上執行 Home Assistant 操作，並先預覽結果。目標必須是該操作適用的對象，因此對開關執行 `light.turn_on` 會被拒絕。`homeassistant.turn_on`、`turn_off`、`toggle` 和 `update_entity` 適用於任何對象。少數操作需要更多條件：

- 重新啟動或停止 Home Assistant 需要重新啟動能力。安裝更新也需要它，並且一律需要您核准。
- 重新載入自動化、腳本、場景、輔助項目或核心設定需要 YAML 設定。
- 操作傳回的資料需要服務回應資料。沒有它，大多數操作仍會執行，但不傳回資料。只傳回資料的操作（例如 `weather.get_forecasts`）會被拒絕。
- 傳送訊息的通知操作需要通知能力。整合自帶的、透過通知實體傳送訊息的操作也需要。手機以外的舊版通知服務一律需要您核准。
- 公告播放需要廣播能力：`assist_satellite.announce`、`start_conversation`、`ask_question`、`music_assistant.play_announcement`、帶 `announce` 的 `media_player.play_media` 和 `tts.speak`。
- `tts.speak` 還需要對其指定的每個喇叭有 W。一般播放只需要 W。
- ESPHome 裝置操作只需要裝置上的 W。MESA 不檢查它們，也不檢查重新載入或重新啟動。
- 搜尋或列出 Music Assistant 音樂庫需要服務回應資料，以及某個 Music Assistant 實體（例如喇叭）上的 R。這些操作不會播放任何內容，所以 MESA 也不檢查它們。
- `update.install` 必須在裝置的每個實體上通過 MESA。在 ESPHome 裝置上還需要 ESPHome 韌體刷寫。
- 在腳本或自動化的輸入中指定的實體需要與目標相同的存取權限，MESA 也會檢查它。只要有一個不符合，就不會執行。
- 任何形如實體 ID 或 32 字元登錄 ID 的輸入都算作指定了實體。
- 選擇裝置、分區、樓層或標籤的輸入會被拒絕。`all` 也會被拒絕，除非腳本把該輸入宣告為一般資料。請逐一指定實體。

沒有唯一 ID 的實體（例如 YAML 燈或沒有 `id` 的自動化）不能作為目標。Home Assistant 不為它保留登錄檔條目，因此 Phoenix 會對所有金鑰拒絕該操作，並告訴智慧體原因。

需要多個能力的操作遵循最嚴格的那個。拒絕存取會拒絕它，確認會讓它等待你的核准。

未宣告目標的操作（例如 `shell_command`、`rest_command` 或 `python_script`）對每個金鑰都會被拒絕，包括直通金鑰。智慧體指定什麼目標都一樣，即使是該操作所屬網域的實體。沒有目標的其他操作也會被拒絕。

自訂操作由 Home Assistant 在其實體上執行，或其註冊的結構描述列出 `entity_id` 時，算作已宣告目標。

行事曆變更、`homeassistant.reload_config_entry` 和 `todo.remove_completed_items` 同樣會被拒絕，因為它們有自己的工具。執行超過 30 秒的操作會被回報為可能未完成。如果 Home Assistant 表示操作失敗，智慧體會收到原因，並被告知在重試前先檢查結果。

允許它的能力：[{{label:caps.cap_restart.label}}](guide:window.capabilities#restart)、[{{label:caps.cap_yaml_edit.label}}](guide:window.capabilities#yaml-edit)、[{{label:caps.cap_service_response.label}}](guide:window.capabilities#service-response)、[{{label:caps.cap_notify.label}}](guide:window.capabilities#notify)、[{{label:caps.cap_search.label}}](guide:window.capabilities#search)（用於預覽）

## 清單和行事曆 {#lists}

智慧體可以新增、變更和移除待辦事項，也可以清除已完成的事項，這只需要清單上的 W。移除事項是永久性的：Phoenix 不保留副本，因此[無法還原](guide:topic.configurationHistory#not-recorded)。結果會列出移除了哪些事項。仍帶有讀取時留下的 `<redacted>` 標記的事項文字會被拒絕。

如果你核准透過 Phoenix 待辦工具請求的移除，Phoenix 會先重新讀取清單。在請求等待期間發生變化的事項會保留，結果會說明是哪些。如果原始 `todo.remove_item` 操作指定的任一事項在等待期間發生變化，該操作會被拒絕。

智慧體可以建立、變更和刪除行事曆事件，這還需要行事曆寫入。MESA 會檢查待辦和行事曆變更。每次行事曆變更都會留下回執，智慧體之後可以查看。

允許它的能力：[{{label:caps.cap_calendar_write.label}}](guide:window.capabilities#calendar-write)

## 等待變更 {#waiting}

智慧體最多可以等待 30 秒，讓實體發生變化或達到某個狀態，以確認操作生效。等待結束時，權限樹和權限會再次檢查。

允許它的能力：[{{label:caps.cap_config_read.label}}](guide:window.capabilities#config-read)

## Home Companion {#home-companion}

[{{label:personas.home_companion.label}}](guide:window.persona#home-companion)金鑰只提供一個工具。應用程式傳送一個一般請求，例如「關掉樓下的燈」。Phoenix 會用自己的模型執行、檢查結果並回報。

- 它控制燈、風扇、開關、溫控、捲簾、閥門、媒體播放器、吸塵器、加濕器和計時器。
- 它讀取感測器、天氣、行事曆和太陽，但無法變更它們。感測器僅限於溫度、濕度、光照、功率、能耗和電池。
- 門鎖、警報和攝影機不在它的範圍內。
- 每個金鑰一次處理一個請求，每個請求最多 8 個操作和 2 分鐘。
- 直通模式和 **{{label:accessKeys.announceAllLabel}}** 一律關閉。

在 Agent Chat 中它使用聊天的模型。其他所有場合使用語音智慧體的提供者和模型。

相關：[語音智慧體](guide:topic.homeAssistantFeatures#voice-agent)

## 自動化和腳本 {#automations}

智慧體可以讀取、建立、編輯和刪除 Home Assistant 自帶編輯器管理的自動化和腳本。設為確認時，核准會顯示新設定，或編輯前後的內容。每次變更都儲存在[{{label:shell.tabChanges}}](guide:topic.configurationHistory)中。

自動化以 Home Assistant 身分執行，而不是智慧體。其操作不受金鑰權限樹或 MESA 的限制。因此這些能力讓智慧體可以間接控制裝置。請使用確認來逐項審核。

- 只有編輯器自己的檔案會變更。在 YAML 中手動撰寫的自動化和腳本會被拒絕。
- 列出它們需要讀取登錄檔，並且只顯示金鑰可以讀取其實體的那些。
- 藍圖需要管理藍圖能力，以及藍圖所屬類型（自動化或腳本）的能力。

允許它的能力：[{{label:caps.cap_automation_write.label}}](guide:window.capabilities#automation-write)、[{{label:caps.cap_script_write.label}}](guide:window.capabilities#script-write)、[{{label:caps.cap_blueprint_write.label}}](guide:window.capabilities#blueprint-write)

## 場景 {#scenes}

智慧體可以讀取、建立、編輯和刪除 Home Assistant 場景編輯器中的場景。場景中的每個實體都需要權限樹中的 W，即使只是讀取場景。MESA 不檢查場景變更。

場景中的群組也需要 W。如果金鑰被拒絕存取群組的某個成員，或 Phoenix 無法讀取成員，群組就不符合要求。

建立或編輯包含門鎖、警報、捲簾或閥門的場景（直接包含或透過群組包含）還需要物理控制。設為拒絕存取時，變更會被拒絕。設為確認時，它會等待你的核准，並把它們列為 `physical_entities`。

允許它的能力：[{{label:caps.cap_scene_write.label}}](guide:window.capabilities#scene-write)、[{{label:caps.cap_physical_control.label}}](guide:window.capabilities#physical-control)

## 輔助項目 {#helpers}

智慧體可以建立、編輯和刪除輔助項目。包括開關、數字、文字、清單、日期、按鈕、計數器、計時器、排程、區域、標籤和人員。它們還可以管理透過整合設定的輔助項目，例如模板，並變更其設定。

- MESA 會檢查輔助項目變更。MESA 確認會要求自己的核准，即使管理輔助項目為允許。
- 只有區域、標籤和人員遵循權限樹。其他輔助項目僅受能力和 MESA 限制，但下面的設定規則除外。
- 刪除模板輔助項目還需要整合管理。
- 在 YAML 中定義的輔助項目無法變更。

讀取或變更透過整合設定的輔助項目的設定，需要對它擁有的每個實體有 R。否則該輔助項目看起來就像不存在。不擁有任何實體的輔助項目也一樣，除非金鑰是直通金鑰。

提到對金鑰隱藏內容的設定顯示為 `<redacted>`，智慧體不能把它傳回。憑證也這樣顯示。提到隱藏實體或 Phoenix 無法檢查的模板本文會被整個隱藏，對直通金鑰也一樣。

允許它的能力：[{{label:caps.cap_helper_write.label}}](guide:window.capabilities#helper-write)、[{{label:caps.cap_integration_write.label}}](guide:window.capabilities#integration-write)

## 儀表板 {#dashboards}

智慧體可以列出、建立、編輯和刪除儀表板，讀取和搜尋其佈局，變更卡片並列出已安裝的自訂卡片。所有這些都需要管理儀表板，包括讀取。

- 權限樹和 MESA 不限制儀表板變更。金鑰無法讀取的實體會顯示為 `<redacted>`，仍包含此類實體的變更會被拒絕。
- 無法顯示的佈局會被拒絕，對 YAML 模式儀表板的變更也會被拒絕。
- 刪除儀表板前會先儲存其佈局，以便還原。
- 自訂卡片清單來自開啟過面板的瀏覽器，最多每 10 分鐘重新整理一次。

資源，即每個儀表板載入的 JavaScript 和 CSS，需要儀表板資源能力。

允許它的能力：[{{label:caps.cap_lovelace_write.label}}](guide:window.capabilities#lovelace-write)、[{{label:caps.cap_dashboard_resources.label}}](guide:window.capabilities#dashboard-resources)

## 還原變更 {#restore}

智慧體可以放回自己之前對模板輔助項目、藍圖、能源設定、儀表板或儀表板資源所做的變更。它會先要求回執，然後傳送還原，還原會經過該能力一般的核准。智慧體只能看到自己的記錄。

允許它的能力：以下任一項：[{{label:caps.cap_helper_write.label}}](guide:window.capabilities#helper-write)、[{{label:caps.cap_blueprint_write.label}}](guide:window.capabilities#blueprint-write)、[{{label:caps.cap_energy_write.label}}](guide:window.capabilities#energy-write)、[{{label:caps.cap_lovelace_write.label}}](guide:window.capabilities#lovelace-write)、[{{label:caps.cap_dashboard_resources.label}}](guide:window.capabilities#dashboard-resources)

相關：[還原可以放回什麼](guide:topic.configurationHistory#restore)

## MESA 策略 {#mesa}

智慧體可以讀取它們能讀取的實體的 MESA 策略，並查看策略為何如此解析。它們還可以在可寫入的實體上取得短期租約，最長 30 秒。租約會告訴其他人智慧體正在那裡工作；它不授予任何權限。

允許它的能力：[{{label:caps.cap_config_read.label}}](guide:window.capabilities#config-read)

相關：[MESA 如何決定](guide:topic.mesaRules)

## 設定、模板和日誌 {#configuration}

- 讀取設定涵蓋 Home Assistant 的基本設定、能源設定、太陽能預測和藍圖。
- 管理能源儀表板會變更能源設定的一部分。它不能為某一類型新增第二個來源，也會拒絕變更已有多個來源的類型。
- 模板渲染會在個別受限程序中渲染模板。它作用於金鑰可讀取狀態的副本，最長 5 秒。
- 讀取系統日誌涵蓋警告和錯誤、日誌簿和通知。故障日誌和擷取的偵錯記錄還需要診斷。
- 整合日誌級別會變更整合的日誌量，例如開啟偵錯日誌。變更可以在 5 到 120 分鐘後結束、持續到下次重新啟動，或一直保留到清除。

代理只能讀取由代理調高日誌級別的整合的偵錯記錄，直到級別調回或 Home Assistant 重新啟動。你自己開啟的偵錯日誌，代理讀取不到。

需要 Home Assistant 本身的模板函式，例如分區或裝置查詢，不會傳回任何內容。

允許它的能力：[{{label:caps.cap_config_read.label}}](guide:window.capabilities#config-read)、[{{label:caps.cap_energy_write.label}}](guide:window.capabilities#energy-write)、[{{label:caps.cap_template_render.label}}](guide:window.capabilities#template-render)、[{{label:caps.cap_log_read.label}}](guide:window.capabilities#log-read)、[{{label:caps.cap_log_control.label}}](guide:window.capabilities#log-control)

## 探索和診斷 {#diagnostics}

這些工具只讀取。它們幫助智慧體在行動前找到方向。

- 搜尋與發現會搜尋和描述實體、分區及其關聯方式，比較它們，並找出可用的操作。
- 讀取登錄檔會列出分區、樓層、區域、裝置、分類、自動化、腳本、場景和輔助項目。
- 自動化追蹤會讀取自動化的執行方式。
- 診斷會檢查設定並讀取系統健康狀況、修復項、無線網路和 ESPHome 概覽。

只有在 YAML 設定不是拒絕存取時，設定檢查才會指出檔案和行號。

允許它的能力：[{{label:caps.cap_search.label}}](guide:window.capabilities#search)、[{{label:caps.cap_registry_read.label}}](guide:window.capabilities#registry-read)、[{{label:caps.cap_traces.label}}](guide:window.capabilities#traces)、[{{label:caps.cap_diagnostics.label}}](guide:window.capabilities#diagnostics)

## 檔案和 YAML {#files}

讀取檔案讓智慧體讀取 www、themes 和 custom_templates 資料夾中的文字檔案，每個最大 1 MiB。資料夾清單分頁傳回，預設每頁 500 項，最多 1000 項。它不能寫入這些檔案。

YAML 設定讓智慧體讀取你的 YAML 檔案，名稱中含有 secret 的檔案、esphome 資料夾和隱藏檔案除外。

名稱暗示機密的值以及提到機密的註解會顯示為 `<redacted>`，但使用普通名稱的密碼可見。有 YAML 錯誤的檔案不會顯示，智慧體只會得到需要修復的行號。沒有工具會寫入 YAML 設定。

允許它的能力：[{{label:caps.cap_filesystem.label}}](guide:window.capabilities#filesystem)、[{{label:caps.cap_yaml_edit.label}}](guide:window.capabilities#yaml-edit)

## ESPHome {#esphome}

智慧體可以讀取和搜尋 ESPHome 裝置設定，密碼、金鑰、所有請求標頭的值、網址中的憑證以及提到它們的註解都會被遮蔽。

使用 ESPHome Device Builder 附加元件時，它們還可以校驗、編譯、讀取裝置日誌、分析記憶體並檢查連線。沒有該附加元件時，只能讀取和搜尋。

secrets.yaml 中的值無論出現在哪裡都會被遮蔽，即使在網址或註解中。金鑰無法讀取的實體 ID 顯示為 `<redacted-entity>`，在檔案中以及 Device Builder 的輸出和日誌中都是如此。任何智慧體都不能讀取或安裝名稱含 secret 的檔案。

Phoenix 依檔名、檔案宣告的裝置名稱以及任何 `use_address` 或靜態 IP 確定檔案所屬的裝置。僅限部分裝置的金鑰只能讀取這些裝置的檔案。宣告了自己的名稱且不符合任何裝置的檔案只受能力限制。

Phoenix 無法確定所屬裝置的檔案（例如共用套件或已刪除的檔案）需要能存取每個 ESPHome 裝置的金鑰。

編譯、清理和記憶體分析需要裝置上的 W。安裝韌體需要 ESPHome 韌體刷寫、ESPHome 裝置 YAML 和裝置上的 W，並且 MESA 會檢查它。

允許它的能力：[{{label:caps.cap_esphome_yaml.label}}](guide:window.capabilities#esphome-yaml)、[{{label:caps.cap_esphome_flash.label}}](guide:window.capabilities#esphome-flash)

## 整合、裝置和系統 {#system}

- 整合管理會新增、啟用、停用、重新載入和移除整合，並移除裝置。
- 重新設定整合會變更整合的設定。
- 管理實體登錄檔會重新命名實體和裝置，並變更實體 ID、分區、樓層、分類和類別。`recorder.purge_entities` 也需要它，因為該操作會刪除歷史記錄。
- 無線電裝置管理會變更 Zigbee 網路。群組、綁定和裝置屬性還需要物理控制。MESA 會檢查對 Zigbee 裝置的變更。
- 沒有實體的 Zigbee 裝置無從檢查，因此建議模式會發出警告並繼續。MESA 處於強制模式、啟動失敗，或你為該裝置儲存了策略時，則會拒絕。
- 備份會建立和列出備份，但無法還原備份。
- 重新啟動或停止 Home Assistant 會重新啟動它。智慧體會被告知不要自行重試。
- 通知會傳送通知，無論是透過自己的工具還是執行通知操作。

備份最多等待 45 秒，絕不會傳到 Home Assistant Cloud。沒有備份加密金鑰時它不會加密，並且會包含 secrets.yaml。

允許它的能力：[{{label:caps.cap_integration_write.label}}](guide:window.capabilities#integration-write)、[{{label:caps.cap_integration_reconfigure.label}}](guide:window.capabilities#integration-reconfigure)、[{{label:caps.cap_registry_write.label}}](guide:window.capabilities#registry-write)、[{{label:caps.cap_radio_write.label}}](guide:window.capabilities#radio-write)、[{{label:caps.cap_backup.label}}](guide:window.capabilities#backup)、[{{label:caps.cap_restart.label}}](guide:window.capabilities#restart)、[{{label:caps.cap_notify.label}}](guide:window.capabilities#notify)

## 金鑰自身的狀態 {#introspection}

每個金鑰都可以檢查自己的核准請求，最多等待 30 秒以取得其中 50 個，並讀取自己的能力摘要和稽核條目。這些不需要能力。來自其他金鑰的請求看起來就像不存在。

## 在語音、Assist 和 AI 任務中 {#household}

家庭中的任何人都可以對 Assist 或語音智慧體說話。對於不是 Home Assistant 管理員的人，金鑰最多保留五項能力。它們是搜尋與發現、讀取登錄檔、物理控制、廣播和服務回應資料。其餘都算作拒絕存取。

核准和稽核工具也會被隱藏，Home Assistant 僅限管理員的操作會被拒絕。Home Assistant 無法辨識的說話者也會被同樣對待。AI 任務始終如此執行。

相關：[語音智慧體、AI 任務或 Assist 工具提供者](guide:topic.homeAssistantFeatures)、[Assist 和語音智慧體](guide:topic.permissionResolution#household)
