---
{ "id": "wizard.provider", "title": "Setting Up the Model Provider", "parent": "workspace.accessKeys", "order": 104, "keywords": ["Anthropic", "Claude", "DeepSeek", "OpenAI", "ChatGPT", "GPT", "Gemini", "Google", "Grok", "xAI", "Groq", "Kimi", "Moonshot", "Meta", "Llama", "MiniMax", "Mistral AI", "NVIDIA NIM", "Xiaomi MiMo", "Tencent Cloud TokenHub", "OpenCode", "Ollama", "OpenAI-Compatible", "llama.cpp", "Bionic", "LM Studio", "vLLM", "LocalAI", "OpenRouter", "Infron", "Together AI", "Cerebras", "Cloudflare Workers AI", "Microsoft Azure", "Azure", "Azure AI Foundry", "Foundry", "Azure OpenAI", "Workers AI", "Fireworks AI", "Alibaba Cloud Model Studio", "Qwen", "DashScope", "Z.ai", "Zhipu", "GLM", "offline", "local model"], "covers": ["frontend_src/views/OnboardingWizard.tsx"] }
---
Agent Chat sends the conversation, and what the agent's tools read, to the model provider you choose, using your own account.

Related: [What leaves your home](guide:topic.security#leaves-home)

Select an account you've already added. To add one, select the provider, fill in its fields, and select **{{label:settings.agentcliValidate}}**. Most providers need only an API key. Some also ask for a region, and a local Ollama or OpenAI-compatible server needs its address.

After validating, select a model or keep **{{label:settings.agentcliModelAuto}}**. **{{label:settings.agentcliProbeOnAdd}}** is on by default.

**{{label:wizard.tryNow}}** saves the new account, then opens Agent Chat on the new key with a test question already typed in. Phoenix doesn't save the account before then.
