---
{ "id": "topic.homeAssistantFeatures", "title": "Voice Agent, AI Task, or Assist Tool Provider", "parent": "window.settingsHome", "order": 2, "keywords": ["conversation agent", "LLM API", "AI tools", "Control Home Assistant", "Device control", "which model"], "covers": ["custom_components/phoenix_mcp/voice_agent.py", "custom_components/phoenix_mcp/ai_task.py", "custom_components/phoenix_mcp/assist_api.py", "custom_components/phoenix_mcp/assist_gemini.py", "custom_components/phoenix_mcp/assist_schema.py"] }
---
Phoenix can work with Home Assistant's AI features in three ways. The difference is whose model answers, and what the access key controls.

## Voice Agent {#voice-agent}

Phoenix answers Home Assistant conversations itself, with your account and model. What it can do is limited by its access key, and by the permissions of the person speaking.

If you want Phoenix's own model and tools behind voice and Assist, use the Voice Agent.

A reply stops at 8,000 characters and says it was cut. Assist shows the same text.

In the Voice Agent and AI Task, a model reply that asks for more than 64 actions runs none of them.

## AI Task {#ai-task}

Phoenix runs Home Assistant's AI Task actions for automations and scripts, with your account and model. It acts for the household, within its access key. If Home Assistant says who started a task, the task has only that person's rights.

That person needs control of the AI Task entity, even when the task names no entity. An automation that no person started doesn't need that control.

It can also answer Home Assistant's Evaluate action for automations and scripts: see [Decisions](guide:window.settingsHome#ai-decisions).

## Assist Tool Provider {#assist-tool-provider}

Another integration's model answers, and Phoenix only lends it tools. The bound access key decides which tools that model gets, and the Home Assistant user who asked limits them further. Phoenix runs no model here, and uses none of your credit.

If you already have a conversation agent you like and want it to reach your home through Phoenix's controls, use the Assist Tool Provider.

In that agent's **AI tools** setting (**Control Home Assistant** before 2026.11), select **Phoenix MCP (scoped)** and nothing else.

Home Assistant's own **Device control** and **Home Assistant** options don't pass through Phoenix. If you select them as well, they give the model tools that bypass the key, approvals, MESA, and the audit log. The **Home Assistant** option reaches the most: every entity and action, templates, logs, and configuration.

From Home Assistant 2026.11, a conversation keeps the tools it started with. A change to the key's guidance or permissions reaches the model with its next tool call. If the model calls a tool it has lost, Phoenix refuses it straight away. A tool you add needs a new conversation.

Related: [{{label:workspace.homeAssistant}}](guide:window.settingsHome)
