---
{ "id": "window.mesaProfiles", "title": "{{label:workspace.mesaProfiles}}", "parent": "workspace.mesa", "order": 1, "keywords": ["MESA", "profile list"], "sectionKeywords": { "add": ["new profile", "create"], "filters": ["search", "type", "control mode", "enforcement", "inherited"], "list": ["columns", "vendor", "source", "tags"], "delete": ["remove"], "actions": ["rescan", "restore dismissed"], "import": ["archive", "backup", "replace", "upload"], "export": ["archive", "backup", "download"] }, "covers": ["frontend_src/views/MesaWindows.tsx", "frontend_src/views/MesaView.tsx", "frontend_src/shell/mesa.tsx", "custom_components/phoenix_mcp/admin/mesa.py", "custom_components/phoenix_mcp/mesa_core/portability.py"] }
---
Every MESA profile, whatever it covers, in one list. The list updates every 30 seconds while it's shown.

## {{label:mesa.addProfile}} {#add}

Starts a new profile in [{{label:workspace.profileEditor}}](guide:window.mesaEditor). Choose what it covers: an entity, a device, an area, an integration or a domain. Nothing is saved until you select **{{label:mesa.save}}** there.

Related: [Levels](guide:topic.mesaRules#levels)

## {{label:logs.filters}} {#filters}

Narrows the list by **{{label:mesa.colAppliesTo}}**, **{{label:mesa.colControl}}** and **{{label:mesa.fieldEnforcement}}**. Each choice shows how many profiles it matches. **{{label:mesa.modeInherited}}** means the profile leaves control mode unset.

Search matches the ID, name, type and tags, and keeps working while the filters are hidden. Hiding the filters stops them applying until you show them again. Changing a filter closes the open profile.

## The list {#list}

Choose a profile to open it in [{{label:workspace.profileEditor}}](guide:window.mesaEditor). The control mode shows "{{label:mesa.modeEnforced}}" beside it when the profile is enforced.

**{{label:mesa.colTags}}** and **{{label:mesa.colSource}}** are hidden at first; show them from a column's menu. **{{label:mesa.colSource}}** says **{{label:mesa.vendorBadge}}** for a profile an integration supplies with its code.

## {{label:mesa.delete}} {#delete}

Removes a profile. Select **{{label:mesa.delete}}**, then **{{label:common.confirm}}**, or click anywhere else to back out. What it covered then follows its other profiles, or the built-in baseline if none is left.

An integration's own vendor profile comes back the next time Home Assistant starts, unless you've saved your own profile for that integration.

## MESA Actions {#actions}

The menu holds:

- **{{label:mesa.importTitle}}** and **{{label:mesa.exportTitle}}**, below.
- **{{label:mesa.suggestTitle}}**, which shows or hides that window.
- **{{label:mesa.rescan}}**, which looks for new [suggestions](guide:window.mesaSuggestions) and Advisory findings straight away.
- **{{label:mesa.restoreDismissed}}**, which brings back every suggestion you dismissed.
- **{{label:mesa.restoreNotices}}**, which brings back the findings you dismissed in [{{label:workspace.advisory}}](guide:window.mesaAdvisory).

## {{label:mesa.importTitle}} {#import}

Adds profiles from a MESA archive file, such as one you exported. Select **{{label:mesa.browse}}** and choose the file. Phoenix shows what the archive contains before you import.

Profiles that already exist here are left as they are. Turn on **{{label:mesa.replaceExisting}}** to overwrite them instead; the button turns red and reads **{{label:mesa.importAndReplace}}**. There's no further confirmation.

Imported profiles take effect straight away. Phoenix checks each one and lists any it refused. A profile for a target this home doesn't have shows up in [{{label:workspace.advisory}}](guide:window.mesaAdvisory#orphans).

An archive can hold fields Profile Editor can't set, such as declared limits and time windows, and deployment defaults.

Related: [What Profile Editor can't set](guide:topic.mesaRules#not-in-editor)

## {{label:mesa.exportTitle}} {#export}

Downloads every profile as one archive file, with deployment defaults if you have any. Select **{{label:mesa.export}}** to confirm. The file holds your whole agent safety policy, so keep it somewhere safe.
