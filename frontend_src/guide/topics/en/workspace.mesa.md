---
{ "id": "workspace.mesa", "title": "MESA" }
---
MESA describes what each device or entity is, so Phoenix can treat it safely: a profile can make a device confirm-only, or put it off-limits, whatever an access key allows.

- A profile can cover a domain, an integration, an area, a device or a single entity. **{{label:workspace.mesaProfiles}}** lists them all; choose one to open it in **{{label:workspace.profileEditor}}**, or start one with **{{label:mesa.addProfile}}**.
- **{{label:mesa.suggestTitle}}** proposes profiles for devices Phoenix recognizes. Nothing is applied until you choose one, check it in the editor and save it; **{{label:mesa.dismiss}}** takes a suggestion off the list.
- **{{label:workspace.advisory}}** opens over the workspace only while MESA has a finding: an automation it could only partly inspect, a trigger that does not match its declaration, or a profile whose target is gone. Dismissing a finding changes no profile, and **{{label:mesa.restoreNotices}}** in the MESA Actions menu shows dismissed ones again.
- Until **{{label:settings.mesaModeLabel}}** in [Settings](guide:workspace.settings) is **{{label:settings.mesaModeEnforced}}**, most restrictions only warn.
