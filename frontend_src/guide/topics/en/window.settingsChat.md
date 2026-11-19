---
{ "id": "window.settingsChat", "title": "{{label:settings.agentcliCard}}", "parent": "workspace.settings", "order": 2, "keywords": ["chat"], "sectionKeywords": { "chat-global": ["Shift+A", "shortcut", "keyboard"], "chat-memory": ["history", "memory", "context"], "chat-steps": ["steps", "tool calls"], "chat-style": ["tone", "style"] }, "covers": ["frontend_src/components/AgentCliSettings.tsx", "frontend_src/components/CommandRoutingToggle.tsx", "frontend_src/components/ConversationBehaviorControls.tsx"] }
---
Settings for Agent Chat, Phoenix's own chat window. Choose the access key, account, and model in the chat window itself.

## {{label:routing.enableChat}} {#chat-commands}

Carries out simple commands, such as turning on a light, without your AI model. Phoenix uses Home Assistant's own sentence matching, in every installed Assist language. Off by default.

If a decision provider is in use, it handles the commands sentence matching misses. Everything else goes to your AI model as usual.

This doesn't apply to {{label:personas.home_companion.label}} keys, or to messages that aren't plain text.

Related: [Decision provider](guide:window.settingsProviders#decision-provider), [How a command is routed](guide:topic.commandRouting)

## {{label:settings.agentcliGlobalLabel}} {#chat-global}

Lets Agent Chat open on any Home Assistant page, not just in this panel. Press Shift+A to show or hide it. On by default.

Shift+A needs keyboard shortcuts turned on in your Home Assistant profile. In this panel, Shift+A works even when this setting is off.

A change takes effect the next time the page fully loads. The **{{label:settings.killSwitchCard}}** closes Agent Chat everywhere.

## {{label:settings.agentcliMemoryLabel}} {#chat-memory}

How much earlier conversation Phoenix sends with each new message, in characters: 0 to 960,000 (default 200,000). Your new message counts toward it.

Tool results count too, and they're often the largest part. Older turns are dropped whole, oldest first. At 0, no earlier turns are sent.

Characters outside basic Latin, such as Chinese, count as six each.

More memory gives the model more context, but each message costs more. A model with a small context window, such as one running locally, needs a lower value.

Related: [Long messages](guide:window.agentChat#memory-warning)

## {{label:settings.agentcliStepsLabel}} {#chat-steps}

The maximum tool steps the model can take for one message: 3 to 100 (default 20). Steps spent fixing a malformed tool call count too.

At the limit, Agent Chat asks whether to continue. The Voice Agent and AI Task use the same limit, but stop instead.

## {{label:settings.conversationStyle}} {#chat-style}

How replies sound. This changes the wording, not what Phoenix does. {{label:settings.styleDirect}} is the default.

| Style | What you get | Example |
|---|---|---|
| {{label:settings.styleDirect}} | The result first, with no greeting or closing line. | The kitchen lights are on. |
| {{label:settings.styleWarm}} | Speaks to you, acknowledges the request, and ends with a friendly line. | All set, I've turned on the kitchen lights for you. Enjoy your evening! |
| {{label:settings.styleCalmGuide}} | Simple words and numbered steps. Explains terms and says what happens next. | The kitchen lights are on now. If you want them off again, just ask me. |
| {{label:settings.styleLively}} | Upbeat, with vivid words and a playful remark when nothing is at stake. | Lights on! The kitchen is officially open for business. |
| {{label:settings.styleTechnical}} | Entity IDs, action names, attributes, and units. | light.kitchen: off to on via light.turn_on, brightness 255. |
| {{label:settings.styleExplanatory}} | Finishes the task, then explains how it worked and how to do it yourself. | The kitchen lights are on. How this worked: I ran light.turn_on on the two lights in the Kitchen area. |

The styles under {{label:settings.styleEnglishOnly}} play a character, such as {{label:settings.stylePirate}}, {{label:settings.styleNoirDetective}}, or {{label:settings.styleStarshipComputer}}. A character stays in voice for the whole reply, at every detail level. Characters work only in English. A reply in any other language uses {{label:settings.styleDirect}} instead.

Every style drops jokes and character voices for failures, approvals, security questions, and destructive actions. Numbers, names, and whether something happened stay exact.

With {{label:settings.detailConcise}}, replies are at most three short sentences, so the style shows mostly in word choice. How closely a reply follows the style also depends on your AI model.

## {{label:settings.detailLevel}} {#chat-detail}

How much replies say: {{label:settings.detailConcise}} (default), {{label:settings.detailBalanced}}, or {{label:settings.detailDetailed}}.

## {{label:settings.homeFocused}} {#chat-home-focused}

Asks the model to stick to your home and decline other subjects. Off by default.

This is a preference, not a security control: the model is asked, not forced. Its access key decides what it can actually do.

Related: [{{label:shell.tabAccessKeys}}](guide:workspace.accessKeys)

## {{label:routingStats.title}} {#chat-statistics}

**{{label:routingStats.show}}** opens a window that counts how Agent Chat's messages were handled: by sentence matching, the decision provider, or your AI model.

Related: [Reading the statistics](guide:topic.routingStatistics)
