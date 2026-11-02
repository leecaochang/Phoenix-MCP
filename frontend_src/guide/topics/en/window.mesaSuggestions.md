---
{ "id": "window.mesaSuggestions", "title": "{{label:mesa.suggestTitle}}", "parent": "workspace.mesa", "order": 3, "keywords": ["MESA", "recommendations", "unprotected", "blast radius"], "sectionKeywords": { "why": ["lock", "alarm", "garage", "valve", "siren", "water heater", "firmware", "automation", "script", "scene"], "review": ["apply", "accept"], "dismiss": ["hide", "restore", "rescan"] }, "covers": ["frontend_src/views/MesaWindows.tsx", "frontend_src/components/MesaSuggestions.tsx", "custom_components/phoenix_mcp/mesa_suggestions.py", "custom_components/phoenix_mcp/const.py"] }
---
Devices and automations that may need a MESA profile. Phoenix never applies a suggestion itself. Point at a row to read why it's suggested in Help.

## Why something is suggested {#why}

**{{label:mesa.signalUnprotected}}**: a risky device that no profile covers at any level. Locks and alarm panels are suggested as {{label:mesa.modeProhibited}}. Garage doors, doors and gates, valves, sirens, water heaters and firmware updates are suggested as {{label:mesa.modeConfirm}}.

When 5 or more of one kind are uncovered, they become one domain suggestion instead, except garage doors, doors and gates.

**{{label:mesa.signalBlastRadius}}**: an automation, script or scene with no profile that uses a risky device or 8 or more entities. It's suggested as {{label:mesa.modeConfirm}}, so an agent asks before starting it.

Targets written as templates can't be seen, so they're never suggested.

## Acting on a suggestion {#review}

Choose a row to open it in [{{label:workspace.profileEditor}}](guide:window.mesaEditor) as a new profile, filled in with the suggested mode. Check it, change it if you like, and select **{{label:mesa.save}}**. Once saved, the suggestion goes away.

## {{label:mesa.dismiss}} {#dismiss}

Hides a suggestion you don't want, for everyone using this home. Select **{{label:mesa.dismiss}}**, then **{{label:common.confirm}}**.

To see dismissed suggestions again, choose **{{label:mesa.restoreDismissed}}** in the MESA Actions menu. **{{label:mesa.rescan}}**, in the same menu, looks for new suggestions straight away; otherwise the list updates every 30 seconds.
