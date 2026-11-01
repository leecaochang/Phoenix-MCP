---
{ "id": "wizard.provider", "title": "Setting Up the Model Provider", "parent": "workspace.accessKeys", "order": 104 }
---
Agent Chat sends the conversation to the model provider you choose, using your own account.

Pick an account you've already added, or add one: choose the provider, fill in its fields, and select **{{label:settings.agentcliValidate}}**. Most providers need only an API key; some also ask for a region, and a local Ollama server needs its address.

After validating, choose a model or keep **{{label:settings.agentcliModelAuto}}**. The option to check the model's settings is on by default and uses a little API credit.

**{{label:wizard.tryNow}}** saves the new account, then opens Agent Chat on the new key with a test question already typed in. Nothing is saved before then.
