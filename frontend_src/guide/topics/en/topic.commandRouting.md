---
{ "id": "topic.commandRouting", "title": "How a Command Is Routed", "parent": "window.settingsChat", "order": 3, "keywords": ["local", "hybrid", "intent", "sentence matching", "shortcut", "fast path", "TypeSafe", "JEV"], "covers": ["custom_components/phoenix_mcp/routing/chat_routing.py", "custom_components/phoenix_mcp/routing/command_routing.py", "frontend_src/components/CommandRoutingToggle.tsx", "custom_components/phoenix_mcp/routing/command_clarification.py", "custom_components/phoenix_mcp/routing/command_display.py", "custom_components/phoenix_mcp/routing/command_execution.py", "custom_components/phoenix_mcp/routing/command_operations.py", "custom_components/phoenix_mcp/routing/command_summary.py", "custom_components/phoenix_mcp/routing/command_verification.py", "custom_components/phoenix_mcp/routing/decision_batch.py", "custom_components/phoenix_mcp/routing/decision_context.py", "custom_components/phoenix_mcp/routing/decision_history.py", "custom_components/phoenix_mcp/routing/decision_identity.py", "custom_components/phoenix_mcp/routing/decision_provider.py", "custom_components/phoenix_mcp/routing/decision_reference.py", "custom_components/phoenix_mcp/routing/decision_shadow.py", "custom_components/phoenix_mcp/routing/decision_targets.py", "custom_components/phoenix_mcp/entity_commands.py", "custom_components/phoenix_mcp/entity_registration.py", "custom_components/phoenix_mcp/routing/intent_recognition.py", "custom_components/phoenix_mcp/routing/numeric_evidence.py", "custom_components/phoenix_mcp/routing/numeric_recognition.py", "custom_components/phoenix_mcp/routing/temporal_values.py", "custom_components/phoenix_mcp/routing/voice_context.py", "custom_components/phoenix_mcp/routing/routing_tables.py", "custom_components/phoenix_mcp/consumer_identity.py"] }
---
Simple commands like "turn off the kitchen light" don't need a full AI model. When command handling is on, Phoenix tries cheaper ways first.

## The order Phoenix tries {#order}

1. Home Assistant's sentence matching, in every installed Assist language. It runs in your home and costs nothing.
2. The decision provider, if you use one. It handles the commands sentence matching missed.
3. Your AI model, for everything else, just as without command handling.

Once a step carries out a command, the later steps aren't used.

Related: [Decision provider](guide:window.settingsProviders#decision-provider)

## When it applies {#when}

You turn command handling on separately for [Agent Chat](guide:window.settingsChat#chat-commands) and the [Voice Agent](guide:window.settingsHome#voice-commands).

It only applies to plain text messages. Messages for Home Companion keys always go straight to the companion.

## Follow-up messages {#follow-ups}

The first message in a conversation can go to the decision provider. A follow-up only goes there with a TypeSafe / JEV model, in a language the model supports. Otherwise it goes to your AI model.

## Limits {#limits}

The decision provider has a time limit for each request, and an optional hourly limit. If either is reached, the command goes to your AI model, so nothing goes unanswered.

Related: [Connection settings](guide:window.settingsProviders#decision-connection)

## Seeing the effect {#effect}

Routing statistics count how each message was handled, so you can see what command handling saves you.

Related: [Reading the statistics](guide:topic.routingStatistics)
