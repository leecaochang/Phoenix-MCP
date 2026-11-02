---
{ "id": "window.mesaAdvisory", "title": "{{label:workspace.advisory}}", "parent": "workspace.mesa", "order": 4, "keywords": ["MESA", "findings", "notices", "warnings"], "sectionKeywords": { "inspection": ["templates", "blueprint", "partially inspected"], "validation": ["trigger", "condition", "triggers automations"], "orphans": ["orphaned", "missing", "deleted target"], "dismiss": ["hide", "restore notices"], "refresh": ["stale", "update", "out of date"] }, "covers": ["frontend_src/views/MesaWindows.tsx", "frontend_src/shell/mesa.tsx", "custom_components/phoenix_mcp/mesa.py", "custom_components/phoenix_mcp/__init__.py"] }
---
Things MESA noticed while checking your profiles and automations. The window appears only while it has something to show. None of it changes how MESA treats an action.

## Partly inspected automations {#inspection}

Automations that use templates or blueprints, so MESA couldn't read every entity they touch. Open **{{label:mesa.viewAutomationDetails}}** to list them. You don't need to do anything.

## Trigger findings {#validation}

An entity whose profile says **{{label:mesa.fieldTriggersAutomations}}** is {{label:mesa.trigNone}}, but an automation uses it. In a trigger it's an error, in a condition a warning. Each finding says what to change, in the profile or in the automation.

Related: [{{label:mesa.fieldTriggersAutomations}}](guide:window.mesaEditor#triggers-automations)

## Orphaned profiles {#orphans}

Profiles whose entity, device, area or integration no longer exists, such as after you remove a device. An integration counts as gone when it has no entities and isn't loaded. Choose one to open it in [{{label:workspace.profileEditor}}](guide:window.mesaEditor), where you can delete it. Phoenix never deletes them for you.

## {{label:mesa.dismiss}} {#dismiss}

Hides one group of findings in this browser. It comes back when a new finding joins the group. Choose **{{label:mesa.restoreNotices}}** in the MESA Actions menu to see dismissed findings again.

## When findings update {#refresh}

MESA checks again when Home Assistant starts, when automations reload, and when you save, delete or import a profile here, so a finding you've fixed goes away. **{{label:mesa.rescan}}** checks again straight away.
