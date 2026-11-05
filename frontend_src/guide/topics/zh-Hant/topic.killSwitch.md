---
{ "id": "topic.killSwitch", "title": "緊急停止開關會停止什麼", "parent": "window.settingsSafety", "order": 1, "keywords": ["緊急", "停止", "停用", "503", "恐慌"] }
---
緊急停止開關會切斷 AI 透過 Phoenix 接觸你家的所有途徑。它不會復原已經發生的操作。

## 什麼會停止 {#stops}

- 透過 MCP 連線的外部應用程式會被拒絕。已在執行的請求會在下一次檢查時停止。
- Agent Chat 會關閉，且無法再次開啟。
- 語音智慧體只會回覆它無法使用。
- AI 任務實體會變成無法使用。
- Assist 工具提供方不再提供任何工具。
- 核准等待中的請求會改為取消它。設定復原審核是例外：它仍會執行。

## 什麼繼續運作 {#keeps}

此面板繼續運作，讓你可以查看發生了什麼並重新關閉開關。你仍然可以在變更中還原更改、編輯 MESA 策略，內容相關的策略按鈕也會保留。

## 重新啟動之後 {#restart}

如果 Home Assistant 啟動時開關處於開啟狀態，Phoenix 根本不會開啟其連線。關閉開關會開啟連線，不需要重新啟動。

相關：[緊急停止開關](guide:window.settingsSafety#kill-switch)
