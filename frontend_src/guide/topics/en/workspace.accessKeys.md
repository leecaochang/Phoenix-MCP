---
{ "id": "workspace.accessKeys", "title": "Access Keys" }
---
Every agent connects to Phoenix with its own access key, and the key decides what that agent may do in Home Assistant.

- **{{label:accessKeys.setUpAgent}}** walks you through creating a key and connecting a client.
- Select a key in the list to show it in the windows below: **{{label:accessKeys.cardPersona}}**, **{{label:accessKeys.cardCapabilities}}**, **{{label:workspace.keySettings}}** and the **{{label:accessKeys.permissionsTree}}**, with the **{{label:accessKeys.permissionSummary}}** summing up what it may reach.
- **{{label:workspace.keyActions}}** on the toolbar renames, rotates or revokes the selected key.

Capabilities set to confirm send an agent's changes to [Approvals](guide:workspace.approvals) before anything runs.
