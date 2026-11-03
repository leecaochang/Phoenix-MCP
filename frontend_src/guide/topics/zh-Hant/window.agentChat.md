---
{ "id": "window.agentChat", "title": "{{label:agentchat.title}}", "parent": "workspace.my", "order": 5, "keywords": ["聊天", "智慧體", "助理", "對話", "Shift+A"], "sectionKeywords": { "opening": ["開啟", "快速鍵", "Shift+A", "每個頁面", "彈出"], "window": ["最小化", "最大化", "移動", "調整大小", "停駐", "關閉"], "options": ["詳細", "時間戳記", "頁尾", "文字大小"], "connection": ["存取金鑰", "提供者", "模型", "思考", "溫度", "帳戶", "Anthropic", "Claude", "DeepSeek", "OpenAI", "ChatGPT", "GPT", "Gemini", "Google", "Grok", "xAI", "Groq", "Kimi", "Moonshot", "Meta", "Llama", "MiniMax", "Mistral AI", "NVIDIA NIM", "Xiaomi MiMo", "Tencent Cloud TokenHub", "OpenCode", "Ollama", "OpenRouter", "Together AI", "Cerebras", "Fireworks AI", "Alibaba Cloud Model Studio", "Qwen", "DashScope", "Z.ai", "Zhipu", "GLM", "深度求索", "通義千問", "千問", "阿里雲百煉", "百煉", "智譜", "月之暗面", "騰訊雲", "小米"], "conversation": ["傳送", "停止", "步驟", "處理中", "複製", "token", "用量"], "check-in": ["暫停", "繼續", "簽到前步驟"], "images": ["附加", "影像", "照片", "貼上", "圖片"], "approvals": ["核准", "拒絕", "審核", "等待"], "clear": ["清除聊天記錄", "/clear", "新對話"], "privacy": ["儲存", "瀏覽器", "傳送給提供者", "歷史"] } }
---
與 AI 智慧體談論你的家。它會透過你的某個存取金鑰行動，因此只能做該金鑰允許的事，每個操作都會像其他操作一樣被檢查和記錄。

相關：[{{label:settings.agentcliCard}} 設定](guide:window.settingsChat)、[{{label:workspace.aiProviders}}](guide:window.settingsProviders)

## 開啟 Agent Chat {#opening}

選擇工具列上的 **{{label:shell.agentChat}}**。在你有一個存取金鑰和一個 AI 提供者帳戶後，它會出現。Shift+A 也可以開啟和關閉它，除非你正在欄位中輸入。它需要你的 Home Assistant 個人資料中開啟鍵盤快速鍵。

**{{label:settings.agentcliGlobalLabel}}** 開啟時（預設），你在 Home Assistant 中移動時 Chat 會保持開啟。關閉時，Chat 位於 Phoenix 面板中，離開面板會結束正在進行的回覆。

緊急停止開關開啟時，Chat 會關閉且無法開啟。

## 視窗 {#window}

拖曳標題列可移動它，從任意邊緣可調整大小。靠近螢幕邊緣時，邊緣會貼齊。最小化會將它縮成可移動的小列，最大化會填滿螢幕。

**{{label:agentchat.popOut}}** 會把 Chat 移到它自己的瀏覽器視窗，對話會在那裡繼續。在手機上，Chat 會全螢幕開啟。

在電腦上，你還可以把 Chat 停駐到[我的工作區](guide:workspace.my#chat)。關閉 Chat 不會清除對話。

## {{label:agentchat.options}} {#options}

標題列中的選單按鈕：

- **{{label:agentchat.connection}}** 顯示下方的連線欄位。
- **{{label:agentchat.verbose}}** 顯示智慧體呼叫的每個工具、其結果，以及模型在分享時的推理過程。預設為關閉。
- **{{label:agentchat.showTimestamps}}** 在每則訊息下方新增時間。預設為關閉。
- **{{label:agentchat.showFooter}}** 顯示帶有文字大小和 token 用量的頁尾。預設為開啟。
- **{{label:agentchat.decreaseTextSize}}** 和 **{{label:agentchat.increaseTextSize}}** 變更對話文字的大小。

這些選擇會儲存在此瀏覽器中。

## {{label:agentchat.connection}} {#connection}

標題下方的列顯示正在使用的存取金鑰、提供者和模型。選擇其三角形可變更它們。

- **{{label:agentchat.accessKey}}**：智慧體依此金鑰的權限工作。
- **{{label:agentchat.provider}}**：執行智慧體的 AI 提供者帳戶。
- **{{label:agentchat.model}}**：要使用的模型，來自該帳戶的清單。
- **{{label:agentchat.thinking}}** 和 **{{label:agentchat.temperature}}**：僅在模型提供它們時顯示。溫度留空表示使用提供者的預設值。

變更存取金鑰或提供者會開始新對話。回覆執行期間，你無法變更金鑰、提供者或模型。

## 與智慧體交談 {#conversation}

按 Enter 傳送，按 Shift+Enter 換行。智慧體工作時，一列會顯示它在做什麼，以及它等待提供者了多久。**{{label:agentchat.stop}}** 會結束回覆，並把你的訊息放回輸入方塊。

用訊息的複製按鈕複製它。頁尾顯示此對話使用的 token，以及上一次請求占用了模型上下文的多大比例。

## 暫停確認 {#check-in}

在沒有你輸入的若干步驟後，智慧體會暫停並詢問是否繼續。一個步驟是一次對模型的請求加上它要求的工具。選擇 **{{label:agentchat.continue}}** 讓它繼續。

核准或拒絕操作會重設計數。在[{{label:settings.agentcliStepsLabel}}](guide:window.settingsChat#chat-steps)中設定數量。

## 影像 {#images}

貼上影像、把影像拖到視窗上，或使用附加按鈕：每則訊息最多 4 張。Phoenix 會在傳送前縮小大照片。

Home Companion 金鑰無法傳送影像，也無法傳送給只讀取文字的模型。重新載入後不會保留影像，因此智慧體之後無法再次看到它們。

## 聊天中的核准 {#approvals}

操作需要你的核准時，對話中會出現一張卡片。選擇 **{{label:agentchat.approve}}** 或 **{{label:agentchat.reject}}**，或選擇 **{{label:agentchat.review}}** 先在核准中查看完整變更。

智慧體最多等待 15 分鐘。在核准中或從通知核准也會更新卡片。停止、關閉 Chat 或重新載入會取消仍在等待的操作。

相關：[核准如何運作](guide:topic.approvalLifecycle)

## {{label:agentchat.clearHistory}} {#clear}

立即開始新對話，無需詢問。輸入 /clear 也會如此。對話記錄會保留已記錄的內容。

## 保留什麼，以及在哪裡 {#privacy}

對話會以你的 Home Assistant 使用者身分儲存在此瀏覽器中，因此重新載入後仍會存在。最新的訊息，在[{{label:settings.agentcliScrollbackLabel}}](guide:window.settingsChat#chat-memory)範圍內，會隨每則訊息傳送給提供者。除非對話記錄開啟，否則 Phoenix 不在伺服器上保留副本。

只有管理員可以使用 Agent Chat。
