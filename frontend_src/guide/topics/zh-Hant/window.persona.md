---
{ "id": "window.persona", "title": "{{label:accessKeys.cardPersona}}", "parent": "workspace.accessKeys", "order": 2, "keywords": ["角色", "預設能力", "範本"], "sectionKeywords": { "home-companion": ["家庭", "家人"], "esphome": ["韌體"], "maintenance": ["備份"] } }
---
角色會一次設定金鑰的所有能力，以配合某類智慧體。選擇一個角色，然後如有需要，在[{{label:accessKeys.cardCapabilities}}](guide:window.capabilities)中調整個別能力。你的選擇會立即生效。

角色決定智慧體可以使用哪些工具，而不是它可以存取哪些裝置。後者由[{{label:accessKeys.permissionsTree}}](guide:window.permissions)決定。唯一的例外是 Home Companion。

角色不會阻止智慧體控制裝置。除 Home Companion 外，使用任何角色時，智慧體都可以控制你在權限樹中設為 W 的任何內容。只有門鎖、警報控制面板、捲簾和閥門還需要[{{label:caps.cap_physical_control.label}}](guide:window.capabilities#physical-control)。

除 {{label:settings.colorTheme.spartan}} 外的任何[{{label:settings.colorThemeLabel}}](guide:window.settingsGeneral#color-theme)下，三個角色有自己的色彩：{{label:personas.home_companion.label}}為金色，{{label:personas.new_user.label}}為綠色，{{label:personas.custom.label}}為藍色。

## {{label:personas.read_only.label}} {#read-only}

只讀取：設定、模板、系統日誌、搜尋、登錄檔、自動化追蹤、診斷和服務回應資料。它不變更任何設定。

## {{label:personas.voice_assistant.label}} {#voice-assistant}

讀取設定、模板、搜尋和登錄檔，取得服務回應資料，並可以廣播。通知和物理控制需要你的核准。

## {{label:personas.dashboard_designer.label}} {#dashboard-designer}

讀取設定、模板、搜尋、登錄檔以及主題和卡片檔案，並管理儀表板。對能源儀表板和儀表板資源的變更需要你的核准。

## {{label:personas.maintenance.label}} {#maintenance}

讀取設定、模板、日誌、搜尋、登錄檔、追蹤和診斷，並建立備份。重新啟動、實體登錄檔變更和無線電裝置管理需要你的核准。

## {{label:personas.automation_builder.label}} {#automation-builder}

包含所有一般讀取，並管理自動化、腳本、藍圖、場景、輔助項目和行事曆事件。它還可以廣播。通知、物理控制和重新啟動需要你的核准。

## {{label:personas.power_user.label}} {#power-user}

包含自動化建置器的所有內容，並可無需詢問地重新啟動。整合、儀表板、實體登錄檔、無線電、能源儀表板、儀表板資源和備份需要你的核准。

## {{label:personas.home_admin.label}} {#home-admin}

包含進階使用者的所有內容，並讀取主題和卡片檔案以及 ESPHome 裝置 YAML。重新啟動和 YAML 設定也需要你的核准。它不允許攝影機影像、整合日誌級別或 ESPHome 韌體刷寫。

## {{label:personas.new_user.label}} {#new-user}

讀取設定、模板、搜尋和登錄檔，並取得服務回應資料。物理控制需要你的核准。設定精靈從這個角色開始。

## {{label:personas.esphome.label}} {#esphome}

讀取設定、日誌、搜尋、登錄檔、診斷和 ESPHome 裝置 YAML。刷寫韌體需要你的核准；這是唯一允許刷寫的角色。

## {{label:personas.home_companion.label}} {#home-companion}

用於家庭助理。智慧體只取得一個工具，把請求轉交給 Phoenix 自己的模型，並且無論權限樹授予什麼，都只能存取家用裝置。物理控制需要你的核准。

在 Agent Chat 中它使用那裡選擇的帳戶；其他所有場合使用語音智慧體的帳戶和模型。它不能使用直通模式或宣告全部工具：選擇它會將兩者關閉，並在關閉時告知你。變更能力不會使它變為自訂。

## {{label:personas.custom.label}} {#custom}

能力的任意組合。變更個別能力後，角色會變為自訂，或變為設定現在符合的角色。

如果切換離開時角色是自訂，且你尚未重新載入頁面，再次選擇自訂會恢復金鑰之前的自訂設定。
