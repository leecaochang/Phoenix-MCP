---
{ "id": "workspace.mesa", "title": "MESA Workspace", "order": 40, "keywords": ["MESA", "safety", "profiles", "control mode"] }
---
MESA is Phoenix's device safety layer. A profile tells MESA how careful to be with a device or entity: let agents act freely, ask you first, or keep them off it entirely. MESA applies on top of what each access key allows.

- **{{label:workspace.mesaProfiles}}** lists every profile. Choose one to open it, or select **{{label:mesa.addProfile}}** to start a new one. Import and export profiles from the MESA Actions menu.
- **{{label:workspace.profileEditor}}** shows the chosen profile. Change it and save.
- **{{label:mesa.suggestTitle}}** lists devices and automations that may need a profile. Nothing changes until you save one.
- **{{label:workspace.advisory}}** appears only when MESA has a finding to show you.

How strictly MESA acts is set by **{{label:settings.mesaModeLabel}}** in [Settings](guide:window.settingsSafety#mesa-mode). In {{label:settings.mesaModeAdvisory}} mode, the default, most rules only warn the agent.

Related: [How MESA Decides](guide:topic.mesaRules), [Profile buttons on Home Assistant's pages](guide:window.settingsGeneral#inject)
