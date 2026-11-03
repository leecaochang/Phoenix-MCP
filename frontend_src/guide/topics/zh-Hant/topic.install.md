---
{ "id": "topic.install", "title": "安裝 Phoenix MCP", "parent": "topic.about", "order": 10, "keywords": ["安裝", "設定", "HACS", "需求"], "sectionKeywords": { "requirements": ["版本", "最低"], "hacs": ["自訂儲存庫", "下載"], "manual": ["custom_components", "複製"], "setup": ["新增整合", "裝置與服務"], "panel": ["側邊欄", "管理員"], "language": ["翻譯", "16 種語言"], "removing": ["解除安裝", "刪除", "移除整合"] } }
---
Phoenix MCP 是一個 Home Assistant 整合。安裝它、新增一次，它的面板就會出現在側邊欄中。

## 需求 {#requirements}

Home Assistant 2026.7.0 或更新版本。Phoenix 不需要安裝其他東西：它的安全層 MESA 隨附其中。

## 使用 HACS 安裝 {#hacs}

在 HACS 中找到 Phoenix MCP，或把它的儲存庫新增為整合類型的自訂儲存庫。下載後重新啟動 Home Assistant。HACS 會替你檢查 Home Assistant 版本。

## 手動安裝 {#manual}

把 `phoenix_mcp` 資料夾複製到 Home Assistant 的 `custom_components` 資料夾，然後重新啟動 Home Assistant。在較舊的 Home Assistant 上，Phoenix 不會啟動，Home Assistant 日誌會說明它需要哪個版本。

## 新增整合 {#setup}

在 Home Assistant 中，依序開啟設定、裝置與服務、新增整合，然後選擇 Phoenix MCP。沒有需要填寫的內容。它只能新增一次。

## 面板 {#panel}

**Phoenix MCP** 會出現在管理員的側邊欄中。其他所有內容都在那裡設定，首先是存取金鑰。

每個存取金鑰在 Home Assistant 中還會有一個裝置，其感測器會顯示狀態、請求、拒絕、速率限制命中、上次使用和過期時間。智慧體看不到這些感測器。

## 語言 {#language}

面板支援 16 種語言。預設會跟隨你的 Home Assistant 個人資料；可在[{{label:settings.languageLabel}}](guide:window.settingsGeneral#language)中選擇其他語言。此選擇僅適用於目前的瀏覽器。指南會在頁面有翻譯時以你的語言顯示，其餘頁面則以英文顯示。

## 移除 Phoenix MCP {#removing}

刪除整合會撤銷所有存取金鑰、取消等待核准的請求，並清除所有 AI 提供者帳戶及其 API 金鑰。它也會解除 Phoenix 與 Assist、語音智慧體、AI 任務和決策提供者的關聯。

其他所有內容都會保留在 Home Assistant 的儲存空間中。包括緊急停止開關和 MESA 模式等設定，以及稽核日誌、對話記錄、MESA 策略、變更歷史和安全副本。如果你再次新增 Phoenix，它會讀取這些內容，已撤銷的金鑰會列在已歸檔的存取金鑰中。

要同時移除 Phoenix 的資料，請在刪除整合前使用[{{label:settings.wipeTitle}}](guide:window.settingsSafety#wipe-core)中的 **{{label:settings.wipeCoreLabel}}**。
