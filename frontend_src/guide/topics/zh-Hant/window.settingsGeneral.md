---
{ "id": "window.settingsGeneral", "title": "{{label:workspace.general}}", "parent": "workspace.settings", "order": 6, "sectionKeywords": { "language": ["翻譯", "地區設定"], "theme": ["深色模式", "淺色模式"], "color-theme": ["色彩", "調色盤", "外觀", "Spartan", "Wayfinding", "Framed", "Mist", "Office", "Aubergine", "Autumn"], "presets": ["預設"], "inject": ["策略按鈕", "MESA"], "about": ["版本"] } }
---
選擇面板的語言和主題，開啟或關閉選用功能，並查看你的版本。

## {{label:settings.languageLabel}} {#language}

面板的語言。**{{label:settings.languageAuto}}** 會使用你的 Home Assistant 個人資料語言；如果 Phoenix 不支援該語言，則使用英文。每種語言都以自己的名稱列出。

此選擇僅適用於目前的瀏覽器，並會立即變更面板和 Agent Chat。

## {{label:settings.themeLabel}} {#theme}

{{label:settings.themeLight}}、{{label:settings.themeAuto}}（預設）或 {{label:settings.themeDark}}。{{label:settings.themeAuto}} 會跟隨 Home Assistant 的深色模式。此選擇僅適用於目前的瀏覽器。

## {{label:settings.colorThemeLabel}} {#color-theme}

變更整個面板、指南和 Agent Chat 的色彩。每種配色主題都有淺色和深色版本，由 {{label:settings.themeLabel}} 決定你看到哪一個。此選擇僅適用於目前的瀏覽器，並會立即變更所有內容。

- **{{label:settings.colorTheme.wayfinding}}**：類似 {{label:settings.colorTheme.spartan}}，但每個工作區在其工具列按鈕、工具和視窗圖示上有自己的色彩。
- **{{label:settings.colorTheme.autumn}}**：鏽褐色工具列帶襯著暖色紙面，搭配棕褐色視窗標題列和焦橙色按鈕。
- **{{label:settings.colorTheme.aubergine}}**：深梅色工具列帶搭配梅色按鈕，類似流行的團隊聊天應用程式。
- **{{label:settings.colorTheme.office}}**：藍色工具列帶和白色視窗，類似桌面辦公套件。
- **{{label:settings.colorTheme.mist}}**：整體柔和的藍色，搭配藍色按鈕和選取效果。
- **{{label:settings.colorTheme.spartan}}**（預設）：黑白配色，只在有意義的地方使用色彩。
- **{{label:settings.colorTheme.framed}}**：視窗有彩色邊框和標題列，你正在使用的視窗邊框更醒目。

狀態色彩在每個主題中都保持含義：綠色表示允許，紅色表示拒絕存取，琥珀色表示等待你。在 {{label:settings.colorTheme.mist}} 和 {{label:settings.colorTheme.office}} 中，讀取權限會顯示為紫色，因為藍色已經是它們的按鈕色彩。

除 {{label:settings.colorTheme.spartan}} 外的每個主題還會為三個角色著色：{{label:personas.home_companion.label}}為金色，{{label:personas.new_user.label}}為綠色，{{label:personas.custom.label}}為藍色。

## {{label:settings.presetsLabel}} {#presets}

預設會儲存存取金鑰的整套設定，讓你可以切換金鑰的設定。開啟預設會給每個金鑰一個起始預設，並在存取金鑰中顯示 **{{label:accessKeys.cardPresets}}** 視窗。預設為關閉。

每個金鑰最多可以儲存 8 個預設。關閉預設會隱藏它們，但不會刪除它們。

相關：[存取金鑰](guide:workspace.accessKeys)

## {{label:settings.injectLabel}} {#inject}

向 Home Assistant 自己的頁面新增 MESA 策略按鈕。包括實體、自動化、腳本、場景、輔助項目和人員清單，以及裝置、分區和整合頁面。只有管理員能看到它們。預設為關閉。

變更會在頁面下次完整載入時生效。如果按鈕在某個頁面上失敗，它們會關閉直到頁面重新載入，但設定仍保持開啟。

相關：[MESA](guide:workspace.mesa)

## {{label:settings.about}} {#about}

顯示 Phoenix 版本、面板建置版本、Phoenix 支援的最舊 Home Assistant 版本，以及專案儲存庫的連結。
