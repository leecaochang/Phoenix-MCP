---
{ "id": "wizard.provider", "title": "設定模型提供者", "parent": "workspace.accessKeys", "order": 104, "keywords": ["Anthropic", "Claude", "DeepSeek", "OpenAI", "ChatGPT", "GPT", "Gemini", "Google", "Grok", "xAI", "Groq", "Kimi", "Moonshot", "Meta", "Llama", "MiniMax", "Mistral AI", "NVIDIA NIM", "Xiaomi MiMo", "Tencent Cloud TokenHub", "OpenCode", "Ollama", "OpenRouter", "Together AI", "Cerebras", "Fireworks AI", "Alibaba Cloud Model Studio", "Qwen", "DashScope", "Z.ai", "Zhipu", "GLM", "深度求索", "通義千問", "千問", "阿里雲百煉", "百煉", "智譜", "月之暗面", "騰訊雲", "小米", "離線", "本機模型"] }
---
Agent Chat 會使用你自己的帳戶，把對話以及智慧體的工具讀取到的內容傳送給你選擇的模型提供者。[哪些資料會離開你的家](guide:topic.security#leaves-home)提供了更多說明。

選擇一個已新增的帳戶，或新增一個：選擇提供者、填寫其欄位，然後選擇 **{{label:settings.agentcliValidate}}**。大多數提供者只需要 API 金鑰；有些還會要求區域，本機 Ollama 伺服器則需要其位址。

驗證後，選擇一個模型，或保留 **{{label:settings.agentcliModelAuto}}**。檢查模型設定的選項預設為開啟，會消耗少量 API 額度。

**{{label:wizard.tryNow}}** 會儲存新帳戶，然後用新金鑰開啟 Agent Chat，並已輸入一個測試問題。在此之前不會儲存任何內容。
