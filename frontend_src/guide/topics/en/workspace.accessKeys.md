---
{ "id": "workspace.accessKeys", "title": "Access Keys", "order": 10 }
---
Each agent connects to Phoenix with its own access key. The key decides what that agent can do in Home Assistant.

- **{{label:accessKeys.setUpAgent}}** walks you through creating a key and connecting an app.
- Select a key in the list to show its **{{label:accessKeys.cardPersona}}**, **{{label:accessKeys.cardCapabilities}}**, **{{label:workspace.keySettings}}** and **{{label:accessKeys.permissionsTree}}** windows.
- **{{label:accessKeys.permissionSummary}}** sums up everything the key can reach.
- Until you select a key, only the list is shown. The key stays selected when you come back from another workspace.
- Each window grows to fit its content, so the workspace scrolls instead of the windows.
- The **{{label:accessKeys.permissionsTree}}** keeps up with Home Assistant as entities, devices, areas and labels change. **{{label:selectBy.menu}}** narrows it to one area, label or integration.
- **{{label:workspace.keyActions}}** on the toolbar renames, rotates or revokes the selected key.
- **{{label:accessKeys.showArchived}}** opens **{{label:accessKeys.archivedTitle}}** under the list, with the revoked and expired keys. It closes when you select a key.
- If you try another persona, choosing **{{label:personas.custom.label}}** again brings back the key's custom capabilities, until you close the panel.

When a capability is set to confirm, the agent's changes go to [Approvals](guide:workspace.approvals) first, and nothing runs until you decide.
