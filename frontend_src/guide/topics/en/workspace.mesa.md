---
{ "id": "workspace.mesa", "title": "MESA" }
---
MESA describes what each device or entity is, so Phoenix can treat it safely: a profile can make a device confirm-only, or put it off-limits, whatever an access key allows.

- A profile can cover a domain, an integration, an area, a device or a single entity. **{{label:workspace.mesaProfiles}}** lists them all; choose one to open it in **{{label:workspace.profileEditor}}**, or start one with **{{label:mesa.addProfile}}**.
- **{{label:mesa.suggestTitle}}** proposes profiles for devices Phoenix recognizes. Nothing is applied until you choose **{{label:mesa.apply}}**.
- **{{label:workspace.orphanedProfiles}}** and **{{label:workspace.advisory}}** appear only while they have findings. Dismissing a notice changes no profile.
- Until **{{label:settings.mesaModeLabel}}** in [Settings](guide:workspace.settings) is **{{label:settings.mesaModeEnforced}}**, most restrictions only warn.
