---
{ "id": "window.mesaSuggestions", "title": "{{label:mesa.suggestTitle}}", "parent": "workspace.mesa", "order": 3, "keywords": ["MESA", "recommendations", "unprotected", "blast radius"], "sectionKeywords": { "why": ["lock", "alarm", "garage", "valve", "siren", "water heater", "firmware", "automation", "script", "scene"], "review": ["apply", "accept"], "dismiss": ["hide", "restore", "rescan"] }, "covers": ["frontend_src/views/MesaWindows.tsx", "frontend_src/components/MesaSuggestions.tsx", "custom_components/phoenix_mcp/mesa_suggestions.py", "custom_components/phoenix_mcp/const.py", "custom_components/phoenix_mcp/catalog_templates.py"] }
---
Devices and automations that might need a MESA profile. Phoenix never applies a suggestion itself. Point at a row to read why it's suggested in Quick Help.

## Why something is suggested {#why}

**{{label:mesa.signalUnprotected}}**: a risky device that no profile covers at any level. Phoenix suggests {{label:mesa.modeProhibited}} for locks and alarm panels. Phoenix suggests {{label:mesa.modeConfirm}} for garage doors, doors, gates, valves, sirens, water heaters, and firmware updates.

When five or more of one kind are uncovered, they become one domain suggestion instead. This doesn't apply to garage doors, doors, and gates.

**{{label:mesa.signalBlastRadius}}**: an automation, script, or scene with no profile that uses a risky device or eight or more entities. Phoenix suggests {{label:mesa.modeConfirm}} for it, so an agent asks before starting it while MESA is enforced.

Phoenix can't read targets written as templates, so it never suggests them.

## Acting on a suggestion {#review}

Select a row to open it in [{{label:workspace.profileEditor}}](guide:window.mesaEditor) as a new profile, filled in with the suggested mode. Check it, change it if you like, and select **{{label:mesa.save}}**. Once saved, the suggestion goes away.

## {{label:mesa.dismiss}} {#dismiss}

Hides a suggestion you don't want, for every administrator. Select **{{label:mesa.dismiss}}**, then **{{label:common.confirm}}**.

To see dismissed suggestions again, select **{{label:mesa.restoreDismissed}}** in the **{{label:mesa.menu}}** menu. **{{label:mesa.rescan}}**, in the same menu, looks for new suggestions straight away. Otherwise, the list updates every 30 seconds.
