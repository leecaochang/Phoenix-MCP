---
{ "id": "topic.connecting", "title": "連線外部應用程式", "parent": "topic.about", "order": 20, "keywords": ["MCP 用戶端", "連線", "設定", "外部智慧體"], "sectionKeywords": { "commands": ["Claude Code", "Gemini CLI", "Antigravity", "Codex", "Cursor", "DeepSeek Harness", "程式碼片段", "命令"], "credentials": ["機密", "洩漏", "保護", "bearer"], "several-keys": ["多個設定", "多個金鑰", "伺服器名稱"], "connection": ["URL", "端點", "Streamable HTTP", "SSE", "協定", "工作階段"], "reconnect": ["工具已變更", "重新整理工具"], "primer": ["指令", "備註", "技能", "SKILL.md"], "ha-mcp-server": ["Home Assistant MCP Server", "/api/mcp", "託管", "連接器"], "problems": ["401", "403", "429", "503", "未授權", "禁止", "疑難排解"] } }
---
外部應用程式，例如程式開發助理或桌面聊天應用程式，會使用一個存取金鑰連線到 Phoenix。應用程式自己的模型負責思考，Phoenix 決定它可以做什麼。

## 取得設定命令 {#commands}

Phoenix 會顯示 Claude Code、Gemini CLI、Antigravity CLI、Codex、Cursor 和 DeepSeek Harness 的確切命令或設定，並為其他 MCP 應用程式提供通用版本。它們會出現在三個位置：

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

## 工具變更時 {#reconnect}

有些應用程式只在連線時讀取工具清單。在你變更金鑰的能力或權限後，請重新連線應用程式以查看新清單。Phoenix 也會在智慧體下次呼叫時告知它：工具已變更時請重新連線；如果只是需要核准的操作變了，則告知其工具沒有變更。

相關：[重新連線智慧體](guide:window.accessKeys#reconnect)

## 智慧體會收到什麼 {#primer}

應用程式連線時，Phoenix 會向它傳送簡短的安全工作說明：核准和試運作如何運作、哪些操作需要你的核准，以及金鑰的備註。Home Companion 金鑰會收到更短的固定版本。

支援技能的應用程式還可以安裝 Phoenix MCP 技能，這是較長的指南，顯示在 **{{label:wizard.skillSectionTitle}}** 下。它對所有人都是相同的，不包含關於你家的任何資訊。

## 透過 Home Assistant 的 MCP Server {#ha-mcp-server}

登入 Home Assistant 本身的應用程式，例如託管連接器，可以透過 Home Assistant 的 MCP Server 存取 Phoenix 的工具，位址為 /api/mcp/phoenix_mcp。它們使用綁定到 [Assist 工具提供方](guide:topic.homeAssistantFeatures#assist-tool-provider)的金鑰，並且需要管理員登入。

在這條路徑上，金鑰的速率限制不適用，稽核日誌會把呼叫顯示為 Assist，應用程式只有主動要求時才會收到 Phoenix 的說明。

## 無法連線時 {#problems}

- **未授權 (401)**：金鑰錯誤、已撤銷或已過期，或者被放在 URL 中而不是 Authorization 標頭中。
- **禁止 (403)**：某個網頁試圖從其他網站連線。桌面應用程式和命令列應用程式不受影響。
- **請求過多 (429)**：金鑰達到速率限制，或同時開啟的連線過多。應用程式應等待後重試。
- **無法使用 (503)**：緊急停止開關已開啟，或 Phoenix 仍在啟動。
