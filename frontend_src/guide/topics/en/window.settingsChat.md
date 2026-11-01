---
{ "id": "window.settingsChat", "title": "{{label:settings.agentcliCard}}", "parent": "workspace.settings", "order": 2, "keywords": ["chat"], "sectionKeywords": { "chat-global": ["Shift+A", "shortcut", "keyboard"], "chat-memory": ["history", "memory", "context"], "chat-steps": ["steps", "tool calls"], "chat-style": ["tone", "style"] }, "covers": ["frontend_src/components/AgentCliSettings.tsx", "frontend_src/components/CommandRoutingToggle.tsx", "frontend_src/components/ConversationBehaviorControls.tsx"] }
---
Settings for Agent Chat, Phoenix's own chat window. Choose the access key, account and model in the chat window itself.

## {{label:routing.enableChat}} {#chat-commands}

Carries out simple commands, such as turning on a light, without your AI model. Phoenix uses Home Assistant's own sentence matching, in every installed Assist language. Off by default.

If a decision provider is in use, it handles the commands sentence matching misses. Everything else goes to your AI model as usual.

This doesn't apply to Home Companion keys, or to messages that aren't plain text.

Related: [Decision provider](guide:window.settingsProviders#decision-provider), [How a command is routed](guide:topic.commandRouting)

## {{label:settings.agentcliGlobalLabel}} {#chat-global}

Lets Agent Chat open on any Home Assistant page, not just in this panel. Press Shift+A to show or hide it. On by default.

Shift+A needs keyboard shortcuts turned on in your Home Assistant profile. In this panel, Shift+A works even when this setting is off.

A change takes effect the next time the page fully loads. The **{{label:settings.killSwitchCard}}** closes Agent Chat everywhere.

## {{label:settings.agentcliScrollbackLabel}} {#chat-memory}

How many lines of the conversation Phoenix sends with each new message: 0 to 5,000 (default 100).

Older turns are dropped whole, and the newest turn is always sent. At 0, no earlier turns are sent. More history gives the model more context, but each message costs more.

## {{label:settings.agentcliStepsLabel}} {#chat-steps}

The maximum tool steps the model can take for one message: 3 to 100 (default 20). Steps spent fixing a malformed tool call count too.

At the limit, Agent Chat asks whether to continue. The Voice Agent and AI Task use the same limit, but stop instead.

## {{label:settings.conversationStyle}} {#chat-style}

How replies sound: {{label:settings.styleDirect}} (default), {{label:settings.styleWarm}}, {{label:settings.styleCalmGuide}}, {{label:settings.styleLively}} or {{label:settings.styleTechnical}}. This changes the wording, not what Phoenix does.

## {{label:settings.detailLevel}} {#chat-detail}

How much replies say: {{label:settings.detailConcise}} (default), {{label:settings.detailBalanced}} or {{label:settings.detailDetailed}}.

## {{label:settings.homeFocused}} {#chat-home-focused}

Asks the model to stick to your home and decline other subjects. Off by default.

This is a preference, not a security control: the model is asked, not forced. Its access key decides what it can actually do.

Related: [Access Keys](guide:workspace.accessKeys)

## {{label:routingStats.title}} {#chat-statistics}

**{{label:routingStats.show}}** opens a window that counts how Agent Chat's messages were handled: by sentence matching, the decision provider, or your AI model.

Related: [Reading the statistics](guide:topic.routingStatistics)
