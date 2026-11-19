---
{ "id": "wizard.choose", "title": "How Your Agent Connects", "parent": "workspace.accessKeys", "order": 100, "covers": ["frontend_src/views/OnboardingWizard.tsx"] }
---
Phoenix is installed. This wizard sets up your first agent and ends with a test you can watch.

This is the Guide. It explains each step here, and later whatever you work on. If it's in your way, drag it by its title bar or resize it from an edge.

- **{{label:wizard.choiceChatTitle}}** runs the agent in a chat window inside Home Assistant. You need an API key from a model provider, or the address of a local Ollama or OpenAI-compatible server.
- **{{label:wizard.choiceAppTitle}}** connects an MCP app you already use, such as Claude Code or an IDE, with a bearer access key.

Either way, the agent gets its own access key. The next steps decide what that key can do. Set up more agents later with **{{label:accessKeys.setUpAgent}}**.
