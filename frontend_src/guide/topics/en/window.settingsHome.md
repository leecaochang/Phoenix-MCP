---
{ "id": "window.settingsHome", "title": "{{label:workspace.homeAssistant}}", "parent": "workspace.settings", "order": 3, "keywords": ["voice", "Assist", "assistant"], "sectionKeywords": { "ai-decisions": ["evaluate", "ai_task.evaluate"], "voice-agent": ["conversation agent", "pipeline"], "voice-assistant": ["pipeline"], "ai-task": ["generate data"], "assist-tool-provider": ["LLM API", "Control Home Assistant"] }, "covers": ["frontend_src/components/VoiceAgentSettings.tsx", "frontend_src/components/AiTaskSettings.tsx", "frontend_src/utils/access_key_list.ts", "frontend_src/components/AiTaskDecisionsSection.tsx", "custom_components/phoenix_mcp/routing/decision_evaluation.py", "frontend_src/components/AssistBridgeSettings.tsx"] }
---
Connect Phoenix to Home Assistant's own AI features. Each tab sets up one feature, and in each one an access key decides what Phoenix may do.

- {{label:settings.voiceCard}}: Phoenix answers Home Assistant's voice and Assist conversations, using your account and model.
- {{label:settings.aiTaskCard}}: Phoenix runs Home Assistant's AI Task actions, using your account and model.
- {{label:settings.assistCard}}: another integration's model uses Phoenix's tools. Phoenix runs no model of its own.

Related: [Which one to use](guide:topic.homeAssistantFeatures)

## {{label:settings.voiceCard}} {#voice-agent}

Makes Phoenix a Home Assistant conversation agent named Phoenix MCP. Set the access key, account and model first: the agent appears only once all three are set.

### {{label:settings.voiceEnableLabel}} {#voice-enable}

Turns the agent on. Off by default. Turning it off also removes the Assist assistant Phoenix created.

While the **{{label:settings.killSwitchCard}}** is on, the agent stays, but only replies that it's unavailable.

### {{label:settings.voiceAssistSetup}} {#voice-assistant}

Creates a Home Assistant assistant named Phoenix MCP that uses this agent, with Home Assistant's default speech-to-text and text-to-speech. Available once the agent is fully set up.

Turning it off deletes the assistant. Home Assistant always keeps at least one assistant, so if it's the only one, it stays, but Phoenix stops managing it.

Phoenix also removes its assistant if you clear the access key, account or model, or revoke the key. Home Assistant keeps it while it is its only assistant, and Phoenix tells you so. Remove it in Home Assistant after adding another.

### {{label:settings.voicePreferredLabel}} {#voice-preferred}

Makes Phoenix's assistant the one Home Assistant uses by default. Turning it off hands that role to the first assistant in Home Assistant's list, which may not be the one you used before.

### {{label:settings.accessKey}} {#voice-access-key}

The access key that decides what the agent may do. You can only choose an active key. The list updates when you create or revoke a key. If the key is revoked or expires, it's cleared here.

A Home Companion key hands conversations to the companion, and hides **{{label:routing.enableVoice}}**.

### {{label:settings.providerAccount}} {#voice-provider}

The account whose model answers. Decision provider accounts aren't listed. Add an account in [{{label:workspace.aiProviders}}](guide:window.settingsProviders) first.

### {{label:settings.model}} {#voice-model}

The model that answers. Choose the account first. If the provider retires the model, Phoenix switches to its successor.

### {{label:routing.enableVoice}} {#voice-commands}

Carries out simple commands without your AI model, as in Agent Chat. Off by default.

Related: [How a command is routed](guide:topic.commandRouting)

### {{label:settings.conversationStyle}} {#voice-style}

How replies sound, with the same choices as [Agent Chat](guide:window.settingsChat#chat-style). Default: {{label:settings.styleDirect}}.

### {{label:settings.detailLevel}} {#voice-detail}

How much replies say. Default: {{label:settings.detailConcise}}.

### {{label:settings.homeFocused}} {#voice-home-focused}

Asks the model to stick to your home. This is a preference, not a security control.

### {{label:routingStats.title}} {#voice-statistics}

**{{label:routingStats.show}}** opens a window that counts how voice commands were handled.

Related: [Reading the statistics](guide:topic.routingStatistics)

## {{label:settings.aiTaskCard}} {#ai-task}

Gives Home Assistant an AI Task entity named Phoenix MCP AI Task, which automations and scripts use to generate text or data. It acts for the household, within its access key. If Home Assistant says who started a task, the task has only that person's rights.

### {{label:settings.aiTaskEnableLabel}} {#ai-task-enable}

Turns the entity on. Off by default. The entity exists only while the access key, account and model are all set.

If you clear any of them, Phoenix removes the entity, and any changes you made to it in Home Assistant are lost.

### {{label:settings.aiTaskDefaultLabel}} {#ai-task-default}

Makes Phoenix's entity Home Assistant's default AI Task. This replaces any current default straight away, without asking. Quick Help shows which one it replaces.

Turning it off leaves Home Assistant with no default. The previous default isn't restored.

### {{label:settings.accessKey}} {#ai-task-access-key}

The access key that decides what AI Task may do. It works the same as the [Voice Agent's](guide:#voice-access-key).

### {{label:settings.providerAccount}} {#ai-task-provider}

The account whose model runs the tasks.

### {{label:settings.model}} {#ai-task-model}

The model that runs the tasks.

### {{label:settings.conversationStyle}} {#ai-task-style}

How text results sound. Default: {{label:settings.styleDirect}}.

### {{label:settings.detailLevel}} {#ai-task-detail}

How much text results say. Default: {{label:settings.detailBalanced}}. Tasks that ask for structured data use their own format instead.

## {{label:settings.aiDecisionsHeading}} {#ai-decisions}

Gives Home Assistant a second entity, Phoenix MCP Decisions, for the Evaluate action. Automations and scripts use it to ask yes-or-no, choice and rating questions about text or data they pass in. Your decision provider answers, and each answer comes with probabilities.

This section shows only on Home Assistant 2026.11 or later, while a decision provider is in use. To add or choose one, go to [{{label:workspace.aiProviders}}](guide:window.settingsProviders#decision-provider).

The questions and the data passed in go to your decision provider, and every request sent counts towards your [request limit](guide:window.settingsProviders#decision-request-limit). An automation that acts on an answer runs as Home Assistant, outside Phoenix's checks.

A person who starts an Evaluate action needs control of the Decisions entity, even when the action names no entity. An automation no person started doesn't. Each call and refusal goes in the [audit log](guide:topic.logRecords#audit-entries).

### {{label:settings.aiDecisionsEnableLabel}} {#ai-decisions-enable}

Turns the entity on. Off by default. The entity exists only while this is on and an access key is chosen.

Revoking the access key or removing the decision provider turns this off and removes the entity, along with any changes you made to it in Home Assistant.

### {{label:settings.aiDecisionsDefaultLabel}} {#ai-decisions-default}

Makes this Home Assistant's default for Evaluate actions that name no entity. It replaces any current default straight away.

### {{label:settings.accessKey}} {#ai-decisions-access-key}

The access key that lets Evaluate actions run. It's separate from the AI Task's key. It must be active, with **{{label:caps.cap_service_response.label}}** set to {{label:caps.modeAllow}}, which a pass-through key already counts as.

Entity permissions and MESA don't apply here: the action has no target entity and changes nothing.

### {{label:routingStats.aiTaskTitle}} {#ai-decisions-statistics}

**{{label:routingStats.aiTaskShow}}** opens a window that counts the Evaluate actions the entity answered, refused or failed, and the decision tokens they used. See [Evaluate](guide:topic.routingStatistics#evaluate).

## {{label:settings.assistCard}} {#assist-tool-provider}

Lends Phoenix's tools to a model that runs elsewhere, such as a conversation agent set to Control Home Assistant with Phoenix MCP (scoped). Phoenix runs no model of its own here.

### {{label:settings.assistBoundAccessKey}} {#assist-access-key}

The access key whose tools the other model gets. With **{{label:settings.assistNotBound}}** (default), no tools are offered. The same happens while the **{{label:settings.killSwitchCard}}** is on.

Changing the key refuses tool calls from earlier turns, so a conversation can't keep the old key's rights. Home Assistant's MCP Server uses the same key, without the key's rate limit.

Related: [Through Home Assistant's MCP Server](guide:topic.connecting#ha-mcp-server)
