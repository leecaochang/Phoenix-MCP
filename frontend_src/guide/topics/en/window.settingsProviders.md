---
{ "id": "window.settingsProviders", "title": "{{label:workspace.aiProviders}}", "parent": "workspace.settings", "order": 1, "keywords": ["API key", "model", "LLM", "account", "credit", "TypeSafe", "JEV", "Clef", "Gemini Decision"], "sectionKeywords": { "provider-add": ["new account", "Anthropic", "Claude", "DeepSeek", "OpenAI", "ChatGPT", "GPT", "Gemini", "Google", "Grok", "xAI", "Groq", "Kimi", "Moonshot", "Meta", "Llama", "MiniMax", "Mistral AI", "NVIDIA NIM", "Xiaomi MiMo", "Tencent Cloud TokenHub", "OpenCode", "Ollama", "OpenRouter", "Infron", "Together AI", "Cerebras", "Cloudflare Workers AI", "Workers AI", "Fireworks AI", "Alibaba Cloud Model Studio", "Qwen", "DashScope", "Z.ai", "Zhipu", "GLM", "offline", "local model", "price", "pricing", "billing", "free tier", "tokens per minute"], "decision-threshold": ["threshold", "confidence", "probability", "0.90"], "decision-provider": ["TypeSafe", "JEV", "fast path", "OpenCode", "System One", "Gemini Decision", "GPT Luna Decision", "Cloudflare", "Workers AI", "Clef", "Clef Flash", "account ID", "OpenAI Decisions", "gpt-6-luna"], "tool-loading": ["load_tools", "deferred tools", "tool search", "tokens", "too many tools", "context"] }, "covers": ["frontend_src/components/LlmProviderSettings.tsx", "frontend_src/components/ProviderAddForm.tsx", "frontend_src/components/DecisionProviderSettings.tsx", "custom_components/phoenix_mcp/tool_loading.py", "custom_components/phoenix_mcp/model_selection.py", "custom_components/phoenix_mcp/routing/decision_budget.py"] }
---
Add the AI accounts Phoenix uses. Each account needs an API key or, for a local model, a server address.

Agent Chat, the Voice Agent and AI Task each use one of these accounts, with a model you choose. They send the conversation and tool results to that provider.

Related: [What leaves your home](guide:topic.security#leaves-home)

## Accounts {#provider-accounts}

Lists every account with its provider and default model. Select an account to show its options, and select it again to hide them.

If something is wrong with an account, such as a model the provider no longer offers, a warning appears in its options.

### {{label:settings.accountChangeModel}} {#change-model}

Choose from the models the provider offers. Models that can't use tools are greyed out, because Phoenix needs tools to act.

**{{label:settings.agentcliModelAuto}}** uses a current model suited to agent work, skipping previews and models announced for retirement. If you pick a model yourself, Phoenix keeps it until the provider retires it. After that, Phoenix uses the successor the provider names, or else the newest model of the same family, or else of the same tier, such as another Haiku model.

If there's none, Phoenix keeps the retired model and shows a warning, so choose another. A model whose name ends in `:free` switches only to another free model. On a local Ollama server, Phoenix switches only within the family, and **{{label:settings.agentcliModelAuto}}** never chooses a cloud model.

When **{{label:settings.agentcliVerifyOptions}}** is on, saving also checks which options the model supports. This uses a little API credit.

### {{label:settings.accountRefreshModels}} {#refresh-models}

Reloads the provider's list of models. This uses no tokens. On a local Ollama server, Phoenix asks about each model in turn.

### {{label:settings.accountCheckOptions}} {#check-options}

Finds out which effort levels the model supports and whether it can read images, by sending it a few very short requests.

This uses a little credit, so Phoenix asks first. Hover over the button to see when it last ran.

### Removing an account {#remove-account}

Removing an account deletes its stored key. You confirm in place first.

If the Voice Agent or AI Task used the account, they lose it, and Phoenix removes the Assist assistant and AI Task entity it created for them.

Related: [Voice Agent](guide:window.settingsHome#voice-agent), [AI Task](guide:window.settingsHome#ai-task)

### {{label:settings.toolLoadingLabel}} {#tool-loading}

Decides how Agent Chat, the Voice Agent and AI Task give this account's model the access key's tools.

**{{label:settings.toolLoadingAuto}}** (default) sends a small set of everyday tools and lists the rest by group. The model loads more when a request needs them. Phoenix does this when the access key has a large set of tools, about 75 or more. With fewer, it sends them all.

Loading in parts sends far fewer tokens with each request, with the same results. A request that needs a tool not loaded yet takes one extra step. The Voice Agent starts with the device control tools, so spoken commands don't wait for that step.

**{{label:settings.toolLoadingAlways}}** loads in parts whatever the size. **{{label:settings.toolLoadingNever}}** always sends every tool, except to providers that accept only 128 tools.

Tool loading never gives the model more access, Phoenix checks every call as usual. Home Companion keys are not affected by the **{{label:settings.toolLoadingLabel}}** setting.

## Adding an account {#provider-add}

1. Select **{{label:settings.addAccount}}** on the toolbar to show the providers.
2. Pick a provider and fill in its fields. Select **{{label:settings.showSecret}}** to check the API key you pasted.
3. Select **{{label:settings.agentcliValidate}}**. This checks the details with the provider, but saves nothing yet.
4. Choose a model, or keep **{{label:settings.agentcliModelAuto}}**, then select **{{label:settings.agentcliDone}}**.

If you change a field after validating, validate again. Phoenix checks the details once more before it saves the account.

### What each provider asks for {#provider-kinds}

Most providers need only an API key. Some also need to know where your account is:

- OpenAI, Grok, Kimi, Mistral AI and MiniMax: the region your key was issued for.
- Xiaomi MiMo: pay as you go, or your Token Plan region.
- Tencent Cloud TokenHub: the site that issued your key. A key works only on that site.
- OpenCode: the Zen or Console route. You can add a key on one route only.
- Z.ai: Standard API, Coding Plan or China. You can save the same key once for each plan.
- Qwen: your workspace's base URL, a public HTTPS address, as well as the API key.
- Cloudflare Workers AI: your Cloudflare account ID and an API token with Workers AI permissions. Phoenix lists only models marked for tool use. The free plan has a small daily allowance, and some models need Workers Paid.
- Ollama (local): your Ollama server's address. Home Assistant connects to it, so use an address the Home Assistant host can reach. Plain `http://` traffic isn't encrypted; see [What leaves your home](guide:topic.security#leaves-home).

Some plans are for coding tools only: Z.ai's Coding Plan, Xiaomi MiMo's Token Plan, and Alibaba Cloud's Coding Plan and Token Plan. Their terms forbid use from an app such as Home Assistant, and the provider may throttle or block the key. Phoenix warns when you choose one. For Voice, AI Task and Agent Chat, use the provider's pay-as-you-go API.

Groq, Mistral AI and Z.ai accept at most 128 tools per request, and Phoenix applies the same limit to Gemini, Kimi, OpenRouter, Infron and Cloudflare Workers AI. When the access key has more, Phoenix always loads tools in parts for them, whatever the [Tool loading](guide:#tool-loading) setting.

Groq's free tier limits how many tokens each model accepts per minute, and a single Phoenix request is larger than that. Groq refuses it, and waiting doesn't help. Use a paid Groq tier or another provider.

## {{label:settings.catalogChecks}} {#catalog-checks}

Phoenix rereads each account's model list from time to time. This keeps the lists up to date, so **{{label:settings.agentcliModelAuto}}** can move to a newer model.

### {{label:settings.providerChecksLabel}} {#provider-checks}

Once an hour, Phoenix rereads any model list older than the period below. This uses no tokens. On by default.

When it's off, Phoenix still reads a list when it needs one: for a new account, when a provider reports a missing model, and when you switch to **{{label:settings.agentcliModelAuto}}**.

### {{label:settings.providerChecksHoursLabel}} {#provider-checks-hours}

Phoenix will re-check the provider's model list on a schedule: 6 to 720 hours (default 24).

## {{label:routing.providerSection}} {#decision-provider}

A decision provider is a small, fast model that carries out simple commands, so your main AI model doesn't have to. It saves the cost and the wait of a full model turn.

It works with **{{label:routing.enableChat}}** and **{{label:routing.enableVoice}}**. Phoenix tries Home Assistant's own sentence matching first, and the decision provider handles the commands sentence matching misses.

Choose from these decision providers:

- TypeSafe / JEV: TypeSafe's JEV model, with a TypeSafe API key.
- OpenCode / System One: JEV, Gemini Decision or GPT Luna Decision, with an OpenCode Console API key.
- Google / Gemini Decision: Google's Gemini Decision model, with a Gemini API key.
- Cloudflare / Clef: Clef or Clef Flash on Workers AI, with your Cloudflare account ID and an API token.
- OpenAI / Decisions: OpenAI's gpt-6-luna decision model, with an OpenAI API key.

Add an account and select its model. Only one decision provider can be in use at a time. Each account shows its model beside its name.

A decision account is separate from any AI provider account from the same company. You must add its key here again if you already use that key for AI Provider.

Every decision provider except Cloudflare / Clef also handles follow-up messages that depend on the conversation. Clef leaves those to your AI provider. See [Follow-up messages](guide:topic.commandRouting#follow-ups).

The jev-preview model only observes and never carries out commands.

To use it, choose its account. Choose **{{label:routing.noProvider}}** to stop using it; commands then rely on sentence matching alone.

It can also answer Home Assistant's Evaluate action for automations and scripts: see [Decisions](guide:window.settingsHome#ai-decisions).

Related: [How a command is routed](guide:topic.commandRouting)

### {{label:routing.decisionThreshold}} {#decision-threshold}

How sure a decision account must be before it carries out a command, from 0.70 to 1.00 (default 0.90). Select the account to show it. Each account keeps its own.

A lower threshold lets the decision provider handle more commands itself, and routes fewer commands to your AI provider. This can potentially let more mistakes through, as each model differs.

## {{label:routing.connectionSettings}} {#decision-connection}

These limits apply while a decision provider is in use.

### {{label:routing.decisionBudget}} {#decision-timeout}

How long Phoenix waits for the decision provider, from 250 to 30,000 milliseconds (default 5,000). If the prompt times out, the request goes to your main AI model as usual.

### {{label:routing.requestLimit}} {#decision-request-limit}

The maximum decision requests Phoenix can send per hour. Set to 0 (default) for no limit. Every use of the decision provider increases the count, including failed requests.

When the limit is reached, commands go to your main AI model until the hour ends.
