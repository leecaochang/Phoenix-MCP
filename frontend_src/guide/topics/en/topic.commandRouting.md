---
{ "id": "topic.commandRouting", "title": "How a Command Is Routed", "parent": "window.settingsChat", "order": 3, "keywords": ["local", "hybrid", "intent", "sentence matching", "shortcut", "fast path", "TypeSafe", "JEV"], "covers": ["custom_components/phoenix_mcp/routing/chat_routing.py", "custom_components/phoenix_mcp/routing/command_routing.py", "custom_components/phoenix_mcp/routing/clarification_store.py", "custom_components/phoenix_mcp/routing/command_plan.py", "custom_components/phoenix_mcp/routing/command_target.py", "custom_components/phoenix_mcp/routing/decision_types.py", "custom_components/phoenix_mcp/routing/plan_currency.py", "frontend_src/components/CommandRoutingToggle.tsx", "custom_components/phoenix_mcp/routing/command_clarification.py", "custom_components/phoenix_mcp/routing/command_display.py", "custom_components/phoenix_mcp/routing/command_execution.py", "custom_components/phoenix_mcp/routing/command_operations.py", "custom_components/phoenix_mcp/routing/command_summary.py", "custom_components/phoenix_mcp/routing/command_verification.py", "custom_components/phoenix_mcp/routing/decision_batch.py", "custom_components/phoenix_mcp/routing/decision_context.py", "custom_components/phoenix_mcp/routing/decision_history.py", "custom_components/phoenix_mcp/routing/decision_identity.py", "custom_components/phoenix_mcp/routing/decision_provider.py", "custom_components/phoenix_mcp/routing/decision_reference.py", "custom_components/phoenix_mcp/routing/decision_shadow.py", "custom_components/phoenix_mcp/routing/decision_targets.py", "custom_components/phoenix_mcp/entity_commands.py", "custom_components/phoenix_mcp/entity_registration.py", "custom_components/phoenix_mcp/routing/intent_recognition.py", "custom_components/phoenix_mcp/routing/numeric_evidence.py", "custom_components/phoenix_mcp/routing/numeric_recognition.py", "custom_components/phoenix_mcp/routing/temporal_values.py", "custom_components/phoenix_mcp/routing/voice_context.py", "custom_components/phoenix_mcp/routing/routing_tables.py", "custom_components/phoenix_mcp/consumer_identity.py"] }
---
Simple commands like "turn off the kitchen light" don't need a full AI model. When command handling is on, Phoenix tries cheaper ways first.

## The order Phoenix tries {#order}

1. Home Assistant's sentence matching, in every installed Assist language. It runs in your home and costs nothing.
2. The decision provider, if you use one. It handles the commands sentence matching missed.
3. Your AI model, for everything else, just as without command handling.

Once a step carries out a command, the later steps aren't used. When sentence matching or the decision provider changes something, the reply names the action that ran, so you hear what happened. When one reads a state, the reply names the device it read, such as "Desk lamp: Off."

Before the decision provider carries out a command, Phoenix also asks it whether you asked for the action now. If it judges a command negated, quoted, put off, or conditional, the command goes to your AI model instead. For example, "don't turn on the bed light" goes to your AI model.

Related: [Decision provider](guide:window.settingsProviders#decision-provider)

## When it applies {#when}

Turn on command handling separately for [Agent Chat](guide:window.settingsChat#chat-commands) and the [{{label:settings.voiceCard}}](guide:window.settingsHome#voice-commands).

It only applies to plain text messages. Messages for {{label:personas.home_companion.label}} keys always go straight to the companion.

## Follow-up messages {#follow-ups}

Later messages in a conversation can go to the decision provider too. Each one is first checked against the conversation: whether it's a real request, and whether an earlier instruction rules it out. If that check isn't sure, the message goes to your AI model.

A follow-up that says "it" instead of naming the device, such as "Now turn it off", works for one light. It can turn the light on or off, or ask whether it's on.

## Actions the decision provider runs {#risky-actions}

The decision provider picks the action from your words. Phoenix checks that you named the device, but it can't check the action. So the decision provider only runs actions that are easy to undo, or that take their value from your words:

- Reading a state or an attribute of a device you name
- Lights, switches, fans, and media players
- Timers
- Stopping a cover or valve, and pausing or docking a vacuum or mower
- Climate fan, preset, and swing modes, and humidifier modes
- Stepping a counter or number helper up or down, and turning a remote on or off
- A number you say, such as a brightness, temperature, or cover position
- A text you put in quotes, and a select option you name

Everything else goes to your AI model instead, just as when the decision provider isn't sure. That includes locks, sirens, alarms, cameras, automations, scripts, and scenes, and opening or closing covers and valves. Your access key's permissions and approvals still apply there.

## Limits {#limits}

The decision provider has a time limit for each request, and an optional hourly limit. If either limit is reached, the command goes to your AI model.

Related: [Connection settings](guide:window.settingsProviders#decision-connection)

## Seeing the effect {#effect}

Routing statistics count how each message was handled, so you can see what command handling saves you.

Related: [Reading the statistics](guide:topic.routingStatistics)
