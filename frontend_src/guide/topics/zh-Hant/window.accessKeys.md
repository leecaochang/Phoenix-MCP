---
{ "id": "window.accessKeys", "title": "{{label:shell.tabAccessKeys}}", "parent": "workspace.accessKeys", "order": 1, "keywords": ["存取金鑰", "權杖", "bearer", "機密"], "sectionKeywords": { "create-key": ["新金鑰", "新增金鑰"], "key-expiry": ["過期", "有效期"], "rotate": ["新機密", "洩漏", "遺失金鑰"], "revoke": ["停用", "移除金鑰", "刪除金鑰"], "reconnect": ["工具已變更"] } }
---
你的存取金鑰。選擇一個金鑰，即可在其他視窗中顯示其設定。在選擇金鑰之前，只會顯示此清單。

## 清單 {#key-list}

每一列顯示金鑰的名稱、模式和狀態，以及建立、最後變更、過期和最後使用的時間。已撤銷和已過期的金鑰會移到[{{label:accessKeys.archivedTitle}}](guide:window.archivedAccessKeys)。

在清單上方的篩選框中輸入，可依名稱或狀態尋找金鑰。你可以排序、移動、調整大小和隱藏欄；佈局會儲存在此瀏覽器中。

模式欄會顯示限定範圍，略過權限樹的金鑰則顯示 {{label:accessKeys.passThrough}}。

相關：[直通模式會開放什麼](guide:topic.passThrough)

## {{label:accessKeys.createAccessKey}} {#create-key}

工具列上的 **{{label:accessKeys.createAccessKey}}** 會在清單上方開啟一個簡短表單。再次選擇它會關閉表單。

### {{label:accessKeys.nameLabel}} {#key-name}

3 到 32 個字母、數字、底線或連字號。每個名稱必須唯一，忽略大小寫，連字號與底線視為相同。名稱會出現在稽核日誌和核准中，因此請說明哪個智慧體使用該金鑰。

### {{label:accessKeys.expiryLabel}} {#key-expiry}

永不過期（預設），或若干分鐘、小時、天或週。金鑰過期時，Phoenix 會將它歸檔。金鑰建立後無法變更有效期。

### 速率限制 {#key-rate-limits}

**{{label:accessKeys.rateRequestsLabel}}**（預設 60）和 **{{label:accessKeys.rateBurstLabel}}**（預設 10）。兩者之後都可以變更。

相關：[速率限制](guide:window.keySettings#rate-limiting)

### {{label:accessKeys.passThroughMode}} {#key-pass-through}

讓金鑰略過權限樹，從而可以存取所有實體。預設為關閉。建立前請開啟確認。

相關：[直通模式會開放什麼](guide:topic.passThrough)

### 建立金鑰之後 {#key-created}

Phoenix 會詢問你將如何使用新金鑰：

- **{{label:accessKeys.postCreateConnectTitle}}**：你的應用程式的設定步驟，已填入金鑰。
- **{{label:accessKeys.postCreateChatTitle}}**：開啟設定，以便在 Agent Chat、語音智慧體或 AI 任務中使用該金鑰。不會顯示金鑰的值。
- **{{label:accessKeys.postCreateShowTitle}}**：金鑰的值，供複製。

在關閉此對話方塊前複製金鑰。Phoenix 只儲存它的雜湊，無法再次顯示。如果遺失，請輪換金鑰。

## {{label:workspace.keyActions}} {#key-actions}

工具列上的 **{{label:workspace.keyActions}}** 會作用於所選金鑰。除重新命名外，每個操作都會在清單上方要求你確認。

### {{label:workspace.rename}} {#rename}

把金鑰名稱變成文字方塊。按 Enter 儲存，或按 Esc 取消。你也可以按兩下名稱。一般名稱規則適用。

### {{label:accessKeys.rotate}} {#rotate}

為金鑰提供新的機密值並顯示它。其他所有內容保持不變，包括其權限和歷史。舊值會立即失效，因此請更新使用它的每個應用程式。同一對話方塊中的 **{{label:accessKeys.helpConnect}}** 會顯示每個應用程式使用新值的設定。

### {{label:accessKeys.enablePtLabel}} {#enable-pass-through}

讓金鑰略過權限樹。權限樹會被保留：在 {{label:accessKeys.permissionsTree}} 視窗中選擇 **{{label:accessKeys.switchToScoped}}** 可重新啟用它。Home Companion 金鑰不能使用直通模式。

相關：[直通模式會開放什麼](guide:topic.passThrough)

### {{label:accessKeys.revoke}} {#revoke}

永久結束金鑰。它的請求會被拒絕，等待中的核准會被取消。Phoenix 還會將它從語音智慧體、AI 任務和 Assist 工具提供方中移除，並移到 {{label:accessKeys.archivedTitle}}。此操作無法復原。

## 重新連線智慧體 {#reconnect}

當你變更金鑰可以使用的工具時，清單上方的橫幅會要求你重新連線其智慧體。有些應用程式只在連線時讀取工具清單。橫幅只對使用過的金鑰顯示。

在權限樹中首次給金鑰寫入權限，或移除其最後一個寫入權限，也會變更其工具，並同樣顯示橫幅。
