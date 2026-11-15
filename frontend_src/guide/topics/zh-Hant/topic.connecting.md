---
{"id": "topic.connecting", "title": "連線外部應用程式", "parent": "topic.about", "order": 20, "keywords": ["MCP 用戶端", "連線", "設定", "外部智慧體"], "sectionKeywords": {"methods": ["OAuth", "存取金鑰", "哪種方式", "標頭"], "commands": ["Claude Code", "Gemini CLI", "Antigravity", "Codex", "Cursor", "DeepSeek Harness", "程式碼片段", "命令"], "credentials": ["機密", "洩漏", "保護", "bearer"], "several-keys": ["多個設定", "多個金鑰", "伺服器名稱"], "connection": ["URL", "端點", "Streamable HTTP", "SSE", "協定", "工作階段"], "reconnect": ["工具已變更", "重新整理工具"], "primer": ["指令", "備註", "技能", "SKILL.md"], "oauth": ["OAuth", "ChatGPT", "Claude", "自訂連接器", "手機應用程式", "託管", "登入"], "oauth-approve": ["核准", "拒絕", "登入請求", "同意"], "ha-mcp-server": ["Home Assistant MCP Server", "/api/mcp", "Home Assistant API", "裝置控制"], "problems": ["401", "403", "429", "503", "未授權", "禁止", "疑難排解"], "remote": ["遠端存取", "Nabu Casa", "外出", "VPN", "Tailscale", "反向代理", "https"]}}
---
外部應用程式，例如程式開發助理或桌面聊天應用程式，會使用一個存取金鑰連線到 Phoenix。應用程式自己的模型負責思考，Phoenix 決定它可以做什麼。只接受伺服器位址的應用程式會改為[透過 OAuth 登入](guide:#oauth)，而不傳送金鑰。

## 兩種連線方式 {#methods}

連線說明以一個選擇開始：

- **{{label:wizard.methodKey}}**：應用程式在每個請求中都傳送存取金鑰。適用於程式開發助理和其他允許你設定標頭的應用程式。
- **{{label:wizard.methodOAuth}}**：應用程式只接受一個位址，並透過 Home Assistant 登入。適用於 ChatGPT、Claude 及其手機應用程式。不會複製任何機密內容。

如果金鑰已經允許 OAuth，說明會直接開啟 OAuth 方式。每種方式只列出支援它的應用程式。

## 取得設定命令 {#commands}

Phoenix 會顯示 Claude Code、Gemini CLI、Antigravity CLI、Codex、Cursor 和 DeepSeek Harness 的確切命令或設定，並為其他 MCP 應用程式提供通用版本。Claude Code 位於 Claude 分頁，Codex 位於 ChatGPT 分頁。

Codex 的設定還會把工具逾時提高到 330 秒，這樣較長的 ESPHome 建置或審批等待不會被中斷。

在 **{{label:wizard.methodOAuth}}** 下，Phoenix 會顯示 Claude、ChatGPT 和 VS Code 的登入步驟。Claude 分頁也涵蓋 Claude Code，ChatGPT 分頁涵蓋 ChatGPT 桌面應用程式和 Codex CLI。

這些說明會出現在三個位置：

- [{{label:accessKeys.setUpAgent}}](guide:wizard.connect) 的連線步驟。
- **{{label:accessKeys.postCreateConnectTitle}}**，就在你建立金鑰之後。
- **{{label:accessKeys.helpConnect}}**，在你輪換金鑰之後。

命令中包含金鑰本身，Phoenix 只會顯示一次。若要為現有金鑰再次查看這些命令，請輪換該金鑰並更新應用程式。

每個應用程式的分頁都會連結到該應用程式自己的設定指南。之後請重新啟動或重新載入應用程式。

## 保護金鑰安全 {#credentials}

存取金鑰就像密碼，可用於其權限允許的一切。Phoenix 只儲存它的指紋，因此無法再次顯示它。

不要把它放在共用檔案和聊天記錄中。Claude Code、Gemini 和 Codex 的設定命令會把它放在命令列上，如果別人能讀取你的 shell 歷史記錄，請清除它。如果洩漏了，請[輪換](guide:window.accessKeys#rotate)金鑰：舊值會立即失效。

## 使用多個金鑰 {#several-keys}

為每個應用程式或任務分配自己的金鑰，只賦予它需要的權限。每個金鑰在應用程式中都有自己的伺服器名稱，例如 phx-home，因此一個應用程式可以同時持有多個金鑰。

重新命名金鑰不會影響它繼續運作，但新的設定命令會使用新名稱。

## 連線 {#connection}

應用程式會連線到你的 Home Assistant 位址加上 /api/phoenix-mcp，並把金鑰作為持有者權杖傳送。設定命令中的 URL 是你的瀏覽器使用的位址；如果應用程式透過其他方式存取 Home Assistant，請在那裡修改。

Phoenix 透過 Streamable HTTP 使用 MCP。使用 SSE 傳輸的舊應用程式設定無法連線。耗時較長的操作會每 15 秒傳送一則訊息來保持連線。

## 透過 OAuth 登入的應用程式 {#oauth}

ChatGPT、Claude 及其手機應用程式無法傳送存取金鑰。它們只接受伺服器位址，並改為登入。為此，Phoenix 執行自己的登入流程，與 Home Assistant 的登入分開。

1. 在連線說明中選擇 **{{label:wizard.methodOAuth}}**，或開啟金鑰的[金鑰設定](guide:window.keySettings#oauth-sign-in)。開啟 **{{label:oauth.switchLabel}}**。
2. 選擇你的應用程式的分頁，並依照其中的步驟操作。每個應用程式都使用 Phoenix 顯示的位址。如有用戶端 ID 和機密，請留空。
3. 應用程式會在你的瀏覽器中開啟 Home Assistant。以管理員身分登入，然後在 Phoenix 中核准該請求。

Claude、ChatGPT 和 VS Code 可以透過這種方式登入，包括 Claude Code 和 Codex CLI 0.148 或更新版本。Antigravity、Cursor、Gemini CLI 和 DeepSeek Harness 不能；請改為給它們金鑰。

VS Code 每個請求最多接受 128 個工具。權限較多的金鑰可能提供更多工具，因此請在 VS Code 的工具選擇器中關閉部分 Phoenix 工具。

之後，應用程式會使用你為它核准的金鑰，並遵循該金鑰的權限、能力、核准和速率限制。它的請求會以該金鑰的名義顯示在稽核日誌中。

應用程式必須能從網際網路透過 HTTPS 存取 Home Assistant，位址就是 Phoenix 顯示的那個。家庭網路或 VPN 位址對託管應用程式無效。在反向代理或通道之後，Home Assistant 必須信任該代理，才能知道請求是透過 HTTPS 抵達的；純 HTTP 永遠不會提供登入。

應用程式必須能從伺服器位址本身找到登入方式（受保護資源中繼資料，RFC 9728），目前的 MCP 應用程式都是這樣做的。只支援較早 2025-03-26 登入方式的應用程式會進入 Home Assistant 自己的登入，Phoenix 會拒絕由此取得的憑證。

### 核准登入 {#oauth-approve}

Phoenix 會顯示應用程式自稱的名稱、識別它的完整位址，以及它返回的位址。請檢查這些是否與你剛連線的應用程式相符。

選擇應用程式取得的金鑰，然後選擇 **{{label:oauth.approve}}**。只有開啟 OAuth 且登入時段正在開放的金鑰才會列出。你的瀏覽器會回到應用程式，應用程式隨即完成連線。

對於不是你發起的登入，請選擇 **{{label:oauth.deny}}**。Phoenix 會拒絕它無法驗證的應用程式，並且只從公用網際網路位址擷取應用程式的描述。

只有 Home Assistant 管理員可以核准。應用程式只會取得金鑰的權利，絕不會取得你的權利。對話方塊會倒數顯示該金鑰的登入時段，並且只能透過它的按鈕關閉，因為被關掉的請求無法找回。如果時間到了，請從應用程式重新開始。

## 從家庭網路外連線 {#remote}

Phoenix 沒有自己的遠端服務。家庭網路外的應用程式會像瀏覽器一樣存取它：透過你的 Nabu Casa 位址、你自己的網域，或 Tailscale 等 VPN。把設定命令中的 URL 改為該位址，後面加上 /api/phoenix-mcp。

在家庭網路外請使用 https 位址，因為金鑰會隨每個請求傳送。反向代理必須傳遞 Authorization 標頭、允許長時間回應，並傳遞原始主機和 https。託管應用程式從其供應商的伺服器連線，因此家庭網路或 VPN 位址對它們無效。

## 工具變更時 {#reconnect}

有些應用程式只在連線時讀取工具清單。在你變更金鑰的能力或權限後，請重新連線應用程式以查看新清單。Phoenix 也會在智慧體下次呼叫時告知它：工具已變更時請重新連線；如果只是需要核准的操作變了，則告知其工具沒有變更。

相關：[重新連線智慧體](guide:window.accessKeys#reconnect)

## 智慧體會收到什麼 {#primer}

應用程式連線時，Phoenix 會向它傳送簡短的安全工作說明：核准和試運作如何運作、哪些操作需要你的核准，以及金鑰的備註。Home Companion 金鑰會收到更短的固定版本。

支援技能的應用程式還可以安裝 Phoenix MCP 技能，這是較長的指南，顯示在 **{{label:wizard.skillSectionTitle}}** 下。它對所有人都是相同的，不包含關於你家的任何資訊。

## 透過 Home Assistant 的 MCP Server {#ha-mcp-server}

登入 Home Assistant 本身的應用程式也可以透過 Home Assistant 的 Model Context Protocol 整合存取 Phoenix 的工具。對大多數應用程式來說，[OAuth 登入](guide:#oauth)更簡單，並把它們限定在一個金鑰上。在 2026.11 之前，它叫 MCP Server。它們使用綁定到 [Assist 工具提供者](guide:topic.homeAssistantFeatures#assist-tool-provider)的金鑰。

從 2026.8 起，請使用 /api/mcp/phoenix_mcp，它需要管理員登入。Home Assistant 2026.7 沒有這個位址，因此請在該版本上使用 /api/mcp。

從 2026.10 起，/api/mcp 位址遵循該整合的 Require an administrator account 選項。新條目會開啟它，較早的條目會保持關閉。2026.10 之前沒有這個選項，因此任何登入 Home Assistant 的人都可以使用 /api/mcp。非管理員只能使用家庭工具，並受他們自己的 Home Assistant 權限限制。

在這條路徑上，金鑰的速率限制不適用，稽核日誌會把呼叫顯示為 Assist，應用程式只有主動要求時才會收到 Phoenix 的說明。

從 2026.10 起，請檢查該整合的選項。新條目預設開啟 Expose all AI tools，會提供每一組工具，包括 Phoenix 的工具。請關閉它，並在 AI tools 下選擇 Phoenix MCP（限定範圍）。從 2026.11 起，Read-only 選項只給應用程式留下標記為唯讀的工具，但它不能取代金鑰的能力設定。

使用過 Home Assistant 內建的 Home Assistant API 的應用程式會看到 Phoenix 的工具名稱，而不是 Home Assistant 的。Phoenix 沒有設定整合或讀取完整日誌的工具。

在這條路徑上，Home Assistant 其他 API 的工具不經過 Phoenix，因此金鑰、核准、MESA 和稽核日誌都不涵蓋它們。當兩個 API 提供同名工具時，Home Assistant 會加上前綴，例如 `phoenix-mcp__`。

「Home Assistant」這組 AI 工具是 2026.11 為管理員新增的，能觸及一切：任何實體或操作、範本、日誌和設定。若要讓應用程式保持在 Phoenix 的檢查之內，只選擇 Phoenix MCP（限定範圍）。

## 無法連線時 {#problems}

- **未授權 (401)**：金鑰錯誤、已撤銷或已過期，或者被放在 URL 中而不是 Authorization 標頭中。對於已登入的應用程式，登入已結束：請重新核准。
- **禁止 (403)**：某個網頁試圖從其他網站連線。桌面應用程式和命令列應用程式不受影響。
- **請求過多 (429)**：金鑰達到速率限制，或同時開啟的連線過多。應用程式應等待後重試。
- **無法使用 (503)**：緊急停止開關已開啟，或 Phoenix 仍在啟動。
