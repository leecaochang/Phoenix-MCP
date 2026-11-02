---
{ "id": "window.settingsProviders", "title": "{{label:workspace.aiProviders}}", "parent": "workspace.settings", "order": 1, "keywords": ["API key", "model", "LLM", "account", "credit", "TypeSafe", "JEV"], "sectionKeywords": { "provider-add": ["new account", "OpenAI", "Anthropic", "Claude", "Gemini", "Ollama"], "decision-provider": ["TypeSafe", "JEV", "fast path"] }, "covers": ["frontend_src/components/LlmProviderSettings.tsx", "frontend_src/components/ProviderAddForm.tsx", "frontend_src/components/DecisionProviderSettings.tsx"] }
---
Add the AI accounts Phoenix uses. Each account needs an API key or, for a local model, a server address.

Agent Chat, the Voice Agent and AI Task each use one of these accounts, with a model you choose.

## Accounts {#provider-accounts}

Lists every account with its provider and default model. Select an account to show its options, and select it again to hide them.

If something is wrong with an account, such as a model the provider no longer offers, a warning appears in its options.

### {{label:settings.accountChangeModel}} {#change-model}

Choose from the models the provider offers. Models that can't use tools are greyed out, because Phoenix needs tools to act.

**{{label:settings.agentcliModelAuto}}** always uses the provider's newest suitable model. If you pick a model yourself, Phoenix keeps it until the provider retires it, then switches to its successor.

When **{{label:settings.agentcliVerifyOptions}}** is on, saving also checks which options the model supports. This uses a little API credit.

### {{label:settings.accountRefreshModels}} {#refresh-models}

Reloads the provider's list of models. This uses no tokens. On a local Ollama server, Phoenix asks about each model in turn.

### {{label:settings.accountCheckOptions}} {#check-options}

Finds out which effort levels the model supports and whether it can read images, by sending it a few one-token requests.

This uses a little credit, so Phoenix asks first. Hover over the button to see when it last ran.

### Removing an account {#remove-account}

Removing an account deletes its stored key. You confirm in place first.

If the Voice Agent or AI Task used the account, they lose it, and Phoenix removes the Assist assistant and AI Task entity it created for them.

Related: [Voice Agent](guide:window.settingsHome#voice-agent), [AI Task](guide:window.settingsHome#ai-task)

## Adding an account {#provider-add}

1. Select **{{label:settings.addAccount}}** on the toolbar to show the providers.
2. Pick a provider and fill in its fields.
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
- Ollama (local): your Ollama server's address. Home Assistant connects to it, so use an address the Home Assistant host can reach.

OpenAI, Gemini, Groq, Kimi, Mistral AI, OpenRouter and Z.ai accept at most 128 tools per request. Phoenix gives them a smaller set to start with, and loads more when the model asks.

## {{label:settings.catalogChecks}} {#catalog-checks}

Phoenix rereads each account's model list from time to time. This keeps the lists up to date, so **{{label:settings.agentcliModelAuto}}** can move to a newer model.

### {{label:settings.providerChecksLabel}} {#provider-checks}

Once an hour, Phoenix rereads any model list older than the period below. This uses no tokens. On by default.

When it's off, Phoenix still reads a list when it needs one: for a new account, when a provider reports a missing model, and when you switch to **{{label:settings.agentcliModelAuto}}**.

### {{label:settings.providerChecksHoursLabel}} {#provider-checks-hours}

How old a model list can get before Phoenix rereads it: 6 to 720 hours (default 24). Greyed out while checks are off.

## {{label:routing.providerSection}} {#decision-provider}

A decision provider is a small, fast model that carries out simple commands, so your main AI model doesn't have to. It saves the cost and the wait of a full model turn.

It works with **{{label:routing.enableChat}}** and **{{label:routing.enableVoice}}**. Phoenix tries Home Assistant's own sentence matching first, and the decision provider handles the commands sentence matching misses.

Phoenix offers one decision provider, TypeSafe / JEV. Add your account here and select the model.

To use it, choose its account. Choose **{{label:routing.noProvider}}** to stop using it; commands then rely on sentence matching alone.

Related: [How a command is routed](guide:topic.commandRouting)

## {{label:routing.connectionSettings}} {#decision-connection}

These limits apply while a decision provider is in use.

### {{label:routing.decisionBudget}} {#decision-timeout}

How long Phoenix waits for the decision provider, from 250 to 30,000 milliseconds (default 5,000). If the prompt times out, the request goes to your main AI model as usual.

### {{label:routing.requestLimit}} {#decision-request-limit}

The maximum decision requests Phoenix can send per hour. Set to 0 (default) for no limit. Every use of the decision provider increases the count, including failed requests.

When the limit is reached, commands go to your main AI model until the hour ends.
