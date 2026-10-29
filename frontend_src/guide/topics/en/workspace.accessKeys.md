---
{ "id": "workspace.accessKeys", "title": "Access Keys" }
---
Every agent connects to Phoenix with its own access key, and the key decides what that agent may do in Home Assistant.

- **{{label:accessKeys.setUpAgent}}** walks you through creating a key and connecting a client.
- Select a key in the list to show it in the windows below: **{{label:accessKeys.cardPersona}}**, **{{label:accessKeys.cardCapabilities}}**, **{{label:workspace.keySettings}}** and the **{{label:accessKeys.permissionsTree}}**, with the **{{label:accessKeys.permissionSummary}}** summing up what it may reach. Until a key is selected, only the list is shown. The key stays selected when you come back from another workspace, and the list keeps itself up to date while it is shown.
- Each window is as tall as what it shows, so the workspace scrolls instead of the windows.
- The **{{label:accessKeys.permissionsTree}}** follows Home Assistant by itself as entities, devices, areas and labels change. **{{label:selectBy.menu}}** narrows it to one area, label or integration.
- **{{label:workspace.keyActions}}** on the toolbar renames, rotates or revokes the selected key.
- **{{label:accessKeys.showArchived}}** opens **{{label:accessKeys.archivedTitle}}** under the list, holding the revoked and expired keys. It closes again when you select a key.
- Choosing **{{label:personas.custom.label}}** again after trying another persona brings back the key's custom capabilities, until the panel is closed.

Capabilities set to confirm send an agent's changes to [Approvals](guide:workspace.approvals) before anything runs.
