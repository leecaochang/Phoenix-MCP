---
{ "id": "topic.security", "title": "安全", "parent": "topic.about", "order": 40, "keywords": ["安全", "安全性", "保護", "威脅", "機密", "隱私"], "sectionKeywords": { "keys": ["phx_", "bearer", "雜湊", "指紋", "查詢字串", "Origin", "403", "DNS 重繫結"], "hidden": ["清除", "已遮蔽", "敏感屬性", "密碼", "權杖", "攝影機串流", "entity_picture"], "gaps": ["別名下的機密", "內嵌密碼", "日誌", "自由文字"], "untrusted": ["提示注入", "裝置名稱", "指令", "不可信資料"], "request-limits": ["413", "請求體大小", "批次", "速率限制", "429", "Retry-After"], "admin": ["管理員", "長期存取權杖", "管理 API", "Home Assistant MCP Server"], "stored": ["備份", ".storage", "API 金鑰", "加密", "對話"], "checklist": ["最佳實務", "建議", "強化"], "emergency": ["金鑰洩漏", "受損", "緊急停止開關", "輪換"], "removing": ["解除安裝", "清除", "刪除全部"] } }
---
Phoenix 讓 AI 智慧體在你家中工作，因此它的設計目標是只給每個智慧體你允許的內容，並讓機密無法觸及。本頁說明 Phoenix 保護什麼、無法保護什麼，以及你可以怎樣保持安全。

相關：[每個操作要經過什麼](guide:topic.about#checks)、[智慧體可以做什麼](guide:topic.tools)

## 存取金鑰 {#keys}

每個應用程式用自己的存取金鑰連線。Phoenix 只儲存它的指紋，因此金鑰本身只在你建立或輪換它時顯示一次。

- 應用程式必須在 Authorization 標頭中傳送金鑰。放在網址中的金鑰會被拒絕。
- 來自 Home Assistant 之外頁面的瀏覽器請求會被拒絕，因此惡意網站無法透過你的瀏覽器存取 Phoenix。
- 已撤銷或已過期的金鑰會立即失效。已在執行的請求會在下一次檢查時停止。

相關：[保護金鑰安全](guide:topic.connecting#credentials)、[{{label:accessKeys.rotate}}](guide:window.accessKeys#rotate)、[{{label:accessKeys.revoke}}](guide:window.accessKeys#revoke)

## 智慧體永遠看不到什麼 {#hidden}

無論金鑰允許什麼，包括直通模式，Phoenix 都會在智慧體看到之前移除一些資料：

- Phoenix 自己的實體，以及它為每個金鑰建立的感測器。
- 攝影機串流和圖片位址，以及存取權杖。
- 任何名稱暗示機密的屬性，例如包含 password、token、secret、key 或 session 的屬性。

金鑰範圍之外的實體看起來就像不存在。在操作結果和儀表板中，它們會顯示為 `<redacted>`。

## Phoenix 無法攔截什麼 {#gaps}

移除機密是依名稱進行的，因此使用普通名稱的機密會被放過。請把密碼和金鑰放在 secrets.yaml 中，任何智慧體都無法讀取它。

- YAML 設定會原樣傳回你的檔案。直接寫在其中的密碼對智慧體可見。
- 日誌是自由文字。Phoenix 會移除網址中的存取金鑰、權杖和密碼，但日誌行中的其他機密仍可能漏過。

只把這些能力授予需要它們的智慧體。

相關：[檔案和 YAML](guide:topic.tools#files)、[設定、模板和日誌](guide:topic.tools#configuration)

## 家庭資料不可信 {#untrusted}

裝置名稱或狀態可能包含任何人寫的文字，例如「忽略你的指令」。當 Phoenix 向模型描述家庭時，它會把這些資料標記為不可信內容，而不是指令。

這有幫助，但沒有模型是完美的。請把你重視的變更保持在確認狀態，這樣每個變更執行前你都能看到。

相關：[{{label:caps.modeConfirm}}](guide:window.capabilities#modes)

## 請求限制 {#request-limits}

- 請求體最大 1 MB，批次最多 50 個請求。批次中的每個請求都計入速率限制。
- 外部應用程式依金鑰套用速率限制，預設每分鐘 60 個請求、每秒 10 個。Agent Chat、語音、Assist 和 AI 任務不受限制。
- 超過限制的金鑰會被告知何時重試。

相關：[{{label:accessKeys.cardRateLimiting}}](guide:window.keySettings#rate-limiting)

## 管理員這一側 {#admin}

面板及其設定需要 Home Assistant 管理員。存取金鑰無法開啟它們。任何管理員憑據都有效，包括管理員的長期存取權杖，而且它可以建立具有任何能力的金鑰。

請保持管理員數量精簡，並保護他們的權杖。Home Assistant 的 MCP Server 使用管理員登入存取 Phoenix，且不受 Phoenix 的速率限制。請為它綁定範圍較窄的金鑰。

相關：[透過 Home Assistant 的 MCP Server](guide:topic.connecting#ha-mcp-server)

## 什麼儲存在哪裡 {#stored}

Phoenix 會把資料儲存在 Home Assistant 的儲存資料夾中。存取金鑰只以指紋形式儲存。AI 提供者的 API 金鑰和記錄的對話會按原樣儲存在那裡，並未加密。

Home Assistant 備份會包含該資料夾。請把備份和設定資料夾視為機密。

相關：[日誌記錄的內容](guide:topic.logRecords)

## 保持安全 {#checklist}

- 為每個應用程式分配自己的金鑰，只賦予它需要的能力。
- 只對完全信任的智慧體使用直通模式。它還會開放診斷和服務回應資料。
- 把系統日誌讀取、診斷和 YAML 設定留給需要它們的智慧體。
- 在為語音、AI 任務或 Assist 使用的金鑰設定有效期之前，要知道過期會解除它們的關聯。
- 在你不信任的網路上連線應用程式時使用 HTTPS。金鑰會隨每個請求傳輸。

相關：[直通模式會開放什麼](guide:topic.passThrough)

## 出問題時怎麼辦 {#emergency}

如果金鑰洩漏，請輪換它。舊值會立即失效，應用程式需要新的值。不再使用的金鑰請撤銷。

如果智慧體行為異常，請開啟[{{label:settings.killSwitchCard}}](guide:topic.killSwitch)。它會拒絕新的智慧體工作，並在下一次檢查時停止正在執行的請求。它無法復原已經傳送的操作，因此之後請查看[{{label:attention.title}}](guide:window.attention)。

## 完全移除 Phoenix {#removing}

刪除整合會撤銷金鑰，但保留設定、日誌和已歸檔的金鑰。要同時移除 Phoenix 的資料，請先使用 **{{label:settings.wipeCoreLabel}}**。

相關：[移除 Phoenix MCP](guide:topic.install#removing)、[{{label:settings.wipeCoreLabel}}](guide:window.settingsSafety#wipe-core)
