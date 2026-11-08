---
{ "id": "topic.routingStatistics", "title": "Reading Routing Statistics", "parent": "window.settingsChat", "order": 2, "keywords": ["counts", "percentage", "tokens", "usage", "reset"], "covers": ["frontend_src/components/RoutingStatistics.tsx", "custom_components/phoenix_mcp/routing/routing_statistics.py", "custom_components/phoenix_mcp/routing/chat_routing_history.py", "custom_components/phoenix_mcp/routing/decision_metrics.py"] }
---
Routing statistics show how often Phoenix carried out a command without your AI model, and what the rest cost. Agent Chat, the Voice Agent and the Evaluate action are counted separately.

## The headline {#headline}

The percentage at the top is the share of messages handled by sentence matching or the decision provider. The higher it is, the fewer full model turns you paid for.

## The counts {#counts}

Each message is counted once, by its outcome. If a message used your AI model at any point, it counts as sent to it.

- **{{label:routingStats.local}}**: Home Assistant's sentence matching carried it out.
- **{{label:routingStats.decision}}**: the decision provider carried it out.
- **{{label:routingStats.provider}}**: your AI model handled it.
- **{{label:routingStats.pending}}**: waiting for your approval.
- **{{label:routingStats.failed}}** and **{{label:routingStats.cancelled}}**: messages that didn't finish.

A message sent to your AI model can also fail or be cancelled, so the rows don't always add up.

## Evaluate {#evaluate}

The Evaluate window counts the Evaluate actions the [Decisions entity](guide:window.settingsHome#ai-decisions) handled. Its headline is the number of calls, as each one goes to the decision provider. Data generation isn't counted.

- **{{label:routingStats.evaluated}}**: the decision provider answered.
- **{{label:routingStats.refused}}**: the entity turned the call down, for example for a person without control of it, or after the request limit.
- **{{label:routingStats.failed}}** and **{{label:routingStats.cancelled}}**: calls that didn't finish.

It shows decision tokens only.

## Tokens {#tokens}

The token rows total what the decision provider and your AI model reported using. Providers don't always report usage. Requests without a report are counted below the totals.

## Refresh and reset {#refresh-and-reset}

**{{label:common.refresh}}** loads the latest counts. **{{label:routingStats.reset}}** sets this window's counts back to zero, after you confirm. Messages still in progress aren't counted after a reset.

Counts are saved every 30 seconds and survive a restart. Deleting core data in [Data Management](guide:window.settingsSafety#wipe-core) clears them.
