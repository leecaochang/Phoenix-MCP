---
{ "id": "topic.homeAssistantFeatures", "title": "語音智慧體、AI 任務或 Assist 工具提供方", "parent": "window.settingsHome", "order": 2, "keywords": ["對話智慧體", "LLM API", "控制 Home Assistant", "哪個模型"] }
---
Phoenix 可以透過三種方式與 Home Assistant 的 AI 功能協作。差別在於由誰的模型回答，以及存取金鑰控制什麼。

## 語音智慧體 {#voice-agent}

Phoenix 使用你的帳戶和模型，自己回答 Home Assistant 對話。它能做什麼受其存取金鑰以及說話者權限的限制。

如果你希望語音和 Assist 背後使用 Phoenix 自己的模型和工具，請使用它。

## AI 任務 {#ai-task}

Phoenix 使用你的帳戶和模型，為自動化和腳本執行 Home Assistant 的 AI 任務操作。它在自己的存取金鑰範圍內代表整個家庭行動。如果 Home Assistant 告知任務由誰發起，任務只擁有該使用者的權限。

## Assist 工具提供方 {#assist-tool-provider}

由另一個整合的模型回答，Phoenix 只借用工具。綁定的存取金鑰決定該模型取得哪些工具，發起請求的 Home Assistant 使用者會進一步限制它們。Phoenix 在這裡不執行模型，也不使用你的額度。

如果你已有喜歡的對話智慧體，並希望它透過 Phoenix 的控制存取你的家，請使用它。

從 Home Assistant 2026.11 起，一次對話會保留開始時的工具。修改後的備註和權限會隨下一次工具呼叫送達模型，已失去的工具會被立即拒絕。新增的工具需要開始新的對話。

相關：[{{label:workspace.homeAssistant}}](guide:window.settingsHome)
