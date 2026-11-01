---
{ "id": "window.chatRoutingStatistics", "title": "{{label:routingStats.chatWindow}}", "parent": "window.settingsChat", "order": 1, "covers": ["frontend_src/components/RoutingStatistics.tsx"] }
---
Counts how Agent Chat's messages were handled since the last reset: by Home Assistant's sentence matching, the decision provider, or your AI model. Open it with **{{label:routingStats.show}}** in the {{label:settings.agentcliCard}} window, or from **{{label:workspace.options}}**.

Every Agent Chat message counts, even with **{{label:routing.enableChat}}** off. AI Task and external apps aren't counted.

Related: [Reading the statistics](guide:topic.routingStatistics), [Handle chat commands directly](guide:window.settingsChat#chat-commands)
