---
{ "id": "window.settingsSafety", "title": "{{label:workspace.safety}}", "parent": "workspace.settings", "order": 4, "sectionKeywords": { "kill-switch": ["緊急", "停止", "停用"], "mesa-mode": ["MESA", "建議", "強制", "執行"], "data-management": ["清除", "刪除", "重設", "擦除"] } }
---
在緊急情況下停止 Phoenix，選擇 MESA 保護裝置的嚴格程度，並刪除 Phoenix 的資料。

## {{label:settings.killSwitchCard}} {#kill-switch}

**{{label:settings.killSwitchDisableLabel}}** 會立即切斷 AI 透過 Phoenix 接觸你家的所有途徑，無需確認。關閉它以恢復存取。不需要重新啟動。

開啟時，工具列上的警告按鈕會引導你回到此開關。

相關：[緊急停止開關會停止什麼](guide:topic.killSwitch)

## {{label:settings.mesaModeLabel}} {#mesa-mode}

當操作違反策略規則時，Phoenix 的裝置安全層 MESA 會做什麼。預設：{{label:settings.mesaModeAdvisory}}。

- {{label:settings.mesaModeOff}}：略過 MESA，所有操作都被允許。
- {{label:settings.mesaModeAdvisory}}：大多數規則只會警告。隱私規則、唯讀裝置和設為強制的策略仍會阻止。
- {{label:settings.mesaModeEnforced}}：禁止的操作會被阻止，需要確認的操作會等待你的核准。

在任何層級都沒有 MESA 策略的裝置會遵循內建基線：燈被允許，門鎖和警報控制面板被禁止，其他所有都需要核准。匯入的部署預設設定會替換該基線。在依賴 {{label:settings.mesaModeEnforced}} 之前，請先新增策略。

相關：[MESA](guide:workspace.mesa)

## {{label:settings.dataCard}} {#data-management}

刪除 Phoenix 的資料。選擇要刪除的內容，選擇 **{{label:settings.wipeSubmit}}**，然後輸入 WIPE 確認。之後頁面會重新載入。

清除無法復原。有已核准請求仍在執行時，清除會被拒絕。

### {{label:settings.wipeCoreLabel}} {#wipe-core}

刪除所有存取金鑰、待處理核准和實體提示，並把所有設定重設為預設值。這也會關閉緊急停止開關。

它還會刪除稽核日誌、對話記錄、變更歷史、路由統計和速率限制計數。它還會移除 Phoenix 建立的語音助理，除非它是 Home Assistant 唯一的助理。Agent Chat 對話也會被刪除，但僅限此瀏覽器。

安全副本、行事曆變更記錄和儀表板資源記錄會保留。要移除它們，請參閱[完全移除 Phoenix](guide:topic.security#removing)。

### {{label:settings.wipeProvidersLabel}} {#wipe-providers}

刪除所有 AI 和決策提供者帳戶及其金鑰。語音智慧體、AI 任務和命令路由會失去帳戶，Phoenix 會移除它建立的語音助理。

### {{label:settings.wipeMesaLabel}} {#wipe-mesa}

刪除所有層級的每條 MESA 策略，以及你忽略的建議。預設為關閉，因此除非你選擇此項，清除會保留你的裝置規則。
