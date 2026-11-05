---
{ "id": "window.keySettings", "title": "{{label:workspace.keySettings}}", "parent": "workspace.accessKeys", "order": 6, "sectionKeywords": { "guidance": ["備註", "指令", "提示"], "announce-all": ["工具清單", "疑難排解"], "inline-wait": ["核准", "逾時", "待處理"], "assist-exposure": ["公開", "直通模式"], "rate-limiting": ["限流", "每分鐘請求數"] } }
---
所選金鑰的更多設定：給其智慧體的指引、工具如何提供、請求等待核准的時間，以及速率限制。

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

## {{label:accessKeys.cardRateLimiting}} {#rate-limiting}

速率限制僅適用於外部應用程式。Agent Chat、語音智慧體、Assist 和 AI 任務從不受限制。每個欄位在你離開時儲存。

### {{label:accessKeys.rateRequestsLabel}} {#rate-requests}

金鑰在任意一分鐘內可以發出的最大請求數。設為 0 可關閉速率限制。新金鑰從 60 開始。

### {{label:accessKeys.rateBurstLabel}} {#rate-burst}

任意一秒內的最大請求數。設為 0 表示不限制每秒請求數。新金鑰從 10 開始。每分鐘請求數為 0 時顯示為灰色。
