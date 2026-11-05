---
{ "id": "topic.homeAssistantFeatures", "title": "Voice Agent, AI Task or Assist Tool Provider", "parent": "window.settingsHome", "order": 2, "keywords": ["conversation agent", "LLM API", "Control Home Assistant", "which model"], "covers": ["custom_components/phoenix_mcp/voice_agent.py", "custom_components/phoenix_mcp/ai_task.py", "custom_components/phoenix_mcp/assist_api.py", "custom_components/phoenix_mcp/assist_gemini.py", "custom_components/phoenix_mcp/assist_schema.py"] }
---
Phoenix can work with Home Assistant's AI features in three ways. The difference is whose model answers, and what the access key controls.

## Voice Agent {#voice-agent}

Phoenix answers Home Assistant conversations itself, with your account and model. What it can do is limited by its access key, and by the permissions of the person speaking.

Use it if you want Phoenix's own model and tools behind voice and Assist.

## AI Task {#ai-task}

Phoenix runs Home Assistant's AI Task actions for automations and scripts, with your account and model. It acts for the household, within its access key. If Home Assistant says who started a task, the task has only that person's rights.

## Assist Tool Provider {#assist-tool-provider}

Another integration's model answers, and Phoenix only lends it tools. The bound access key decides which tools that model gets, and the Home Assistant user who asked limits them further. Phoenix runs no model here, and uses none of your credit.

Use it if you already have a conversation agent you like, and want it to reach your home through Phoenix's controls.

From Home Assistant 2026.11, a conversation keeps the tools it started with. Changed notes and permissions reach the model with its next tool call, and a tool it has lost is refused straight away. A tool you add needs a new conversation.

Related: [{{label:workspace.homeAssistant}}](guide:window.settingsHome)
