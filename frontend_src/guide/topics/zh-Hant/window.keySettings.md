---
{ "id": "window.keySettings", "title": "{{label:workspace.keySettings}}", "parent": "workspace.accessKeys", "order": 6, "sectionKeywords": { "guidance": ["備註", "指令", "提示"], "announce-all": ["工具清單", "疑難排解"], "inline-wait": ["核准", "逾時", "待處理"], "assist-exposure": ["公開", "直通模式"], "oauth-sign-in": ["OAuth", "ChatGPT", "Claude", "連接器", "登入", "手機應用程式"], "oauth-allow-new": ["倒數計時", "登入時段", "10 分鐘", "停止"], "oauth-sign-ins": ["已登入的應用程式", "登出", "移除應用程式"], "rate-limiting": ["限流", "每分鐘請求數"] } }
---
所選金鑰的更多設定，分布在三個分頁上。**{{label:accessKeys.cardGuidance}}** 保存給其智慧體的備註。**{{label:accessKeys.cardAgentBehavior}}** 設定其工具如何提供、請求等待核准多久，以及速率限制。**{{label:accessKeys.tabOAuth}}** 讓應用程式用此金鑰登入。

## {{label:accessKeys.cardGuidance}} {#guidance}

Phoenix 會把這些備註新增到使用此金鑰的每個智慧體的指令中，例如房間和裝置的名稱，或誰使用該智慧體。最多 2,000 個字元。選擇儲存以保留它們，或選擇 **{{label:accessKeys.notesDiscard}}**。

指引只是建議。它絕不授予存取權限，智慧體可能忽略它。智慧體及其模型提供者會收到它，因此絕不要在這裡填寫密碼或其他機密。

當你變更它時，已連線的智慧體會在下一次工具結果中收到新指引。Phoenix 會隱藏金鑰無法看到的實體 ID，並移除看起來像機密的文字。

## {{label:accessKeys.cardAgentBehavior}} {#agent-behavior}

### {{label:accessKeys.announceAllLabel}} {#announce-all}

向智慧體提供所有 Phoenix 工具，即使此金鑰無法使用它們。對這些工具的呼叫仍會被拒絕。預設為關閉；僅用於疑難排解，因為它會讓模型花費更多並導致呼叫失敗。

### {{label:accessKeys.inlineWaitLabel}} {#inline-wait}

需要核准的請求在應用程式收到「待處理」回覆之前，等待你決定的時間：關閉（預設），或 30 到 180 秒。關閉時，應用程式會立即得知請求正在核准中等待。

這僅適用於外部應用程式。Agent Chat 最多等待你的決定 15 分鐘。語音智慧體、Assist 和 AI 任務會回報變更正在等待核准，然後繼續。如果金鑰已有請求在等待，新請求不會等待。

### {{label:accessKeys.assistExposureLabel}} {#assist-exposure}

把直通金鑰限制為 Home Assistant 中向 Assist 公開的實體。預設為關閉。它只對直通金鑰顯示；限定範圍的金鑰改用權限樹。

## {{label:accessKeys.tabOAuth}} {#oauth-sign-in}

讓只接受伺服器位址的應用程式（例如 ChatGPT、Claude 及其手機應用程式）使用此金鑰，而不需要你把金鑰貼到任何地方。預設為關閉。傳送金鑰的智慧體無論如何都會繼續運作。

當你開啟 **{{label:oauth.switchLabel}}** 時，Phoenix 會顯示 **{{label:oauth.address}}**。在應用程式中用該位址新增自訂連接器，用戶端 ID 留空。應用程式會開啟 Home Assistant，你在那裡核准此次登入。

應用程式需要能從網際網路透過 HTTPS 存取 Home Assistant。如果 Home Assistant 沒有 HTTPS 位址，Phoenix 會說明這一點，並且不會開啟登入。如果你透過未加密的 HTTP 開啟了 Phoenix，開關將無法使用，Phoenix 會提供指向其 HTTPS 位址的連結。請在那裡開啟 Phoenix，再設定和核准登入。

關閉它會讓透過 OAuth 使用此金鑰的每個應用程式登出。輪換或撤銷金鑰也一樣。緊急停止開關會暫停已登入的應用程式，直到你解除它。

相關：[透過 OAuth 登入的應用程式](guide:topic.connecting#oauth)

### {{label:oauth.allowNew}} {#oauth-allow-new}

應用程式只能在你允許後的 10 分鐘內發起登入。開啟開關會開始第一個 10 分鐘。倒數計時會顯示剩餘時間。

核准一次登入會結束登入時段，Phoenix 會顯示哪個應用程式已登入。選擇 **{{label:oauth.stopWindow}}** 可提前結束它。在連線另一個應用程式之前，或當應用程式要求你重新登入時，請選擇 **{{label:oauth.allowNew}}**。在沒有開放登入時段時送達的登入連結不會有作用。

Phoenix 在每個地方顯示的倒數計時都相同，包括連線說明和核准對話方塊。

### {{label:oauth.signInsTitle}} {#oauth-sign-ins}

為此金鑰核准的應用程式，以及各自的返回位址和最後使用時間。已登入的應用程式會在金鑰有效期間保持登入。選擇 **{{label:oauth.remove}}**，再選擇 **{{label:common.confirm}}**，即可登出你不再使用的應用程式；點擊其他地方會取消。一個金鑰最多可有 10 個已登入的應用程式。

在應用程式自己那端刪除它或中斷連線，並不一定會通知 Phoenix。有通知 Phoenix 的應用程式會從此清單中消失；否則它會一直留在這裡，直到你移除它。最後使用時間能看出哪些應用程式已經停止使用。

## {{label:accessKeys.cardRateLimiting}} {#rate-limiting}

速率限制位於 **{{label:accessKeys.cardAgentBehavior}}** 分頁。它們僅適用於外部應用程式。Agent Chat、語音智慧體、Assist 和 AI 任務從不受限制。每個欄位在你離開時儲存。

### {{label:accessKeys.rateRequestsLabel}} {#rate-requests}

金鑰在任意一分鐘內可以發出的最大請求數。設為 0 可關閉速率限制。新金鑰從 60 開始。

### {{label:accessKeys.rateBurstLabel}} {#rate-burst}

任意一秒內的最大請求數。設為 0 表示不限制每秒請求數。新金鑰從 10 開始。每分鐘請求數為 0 時顯示為灰色。應用程式開啟或關閉連線時傳送的訊息不計入此限制，只計入每分鐘限制。
