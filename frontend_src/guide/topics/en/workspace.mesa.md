---
{ "id": "workspace.mesa", "title": "MESA Workspace", "order": 40 }
---
MESA describes what each device or entity is, so Phoenix can handle it safely. A profile can make a device need confirmation, or put it off-limits, whatever an access key allows.

- A profile can cover a domain, an integration, an area, a device or a single entity. **{{label:workspace.mesaProfiles}}** lists them all. Choose one to open it in **{{label:workspace.profileEditor}}**, or start a new one with **{{label:mesa.addProfile}}**.
- **{{label:mesa.suggestTitle}}** proposes profiles for devices Phoenix recognizes. Nothing is applied until you choose one, check it in the editor and save it. **{{label:mesa.dismiss}}** removes a suggestion from the list.
- **{{label:workspace.advisory}}** appears only while MESA has a finding: an automation it could only partly inspect, a trigger that doesn't match its declaration, or a profile whose target is gone.
- Dismissing a finding doesn't change any profile. **{{label:mesa.restoreNotices}}** in the MESA Actions menu shows them again.
- Until **{{label:settings.mesaModeLabel}}** in [Settings](guide:window.settingsSafety#mesa-mode) is **{{label:settings.mesaModeEnforced}}**, most restrictions only warn.
