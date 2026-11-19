---
{ "id": "window.mesaProfiles", "title": "{{label:workspace.mesaProfiles}}", "parent": "workspace.mesa", "order": 1, "keywords": ["MESA", "profile list"], "sectionKeywords": { "add": ["new profile", "create"], "filters": ["search", "type", "control mode", "enforcement", "inherited"], "list": ["columns", "vendor", "source", "tags"], "delete": ["remove"], "actions": ["rescan", "restore dismissed"], "import": ["archive", "backup", "replace", "upload"], "export": ["archive", "backup", "download"] }, "covers": ["frontend_src/views/MesaWindows.tsx", "frontend_src/views/MesaView.tsx", "frontend_src/shell/mesa.tsx", "custom_components/phoenix_mcp/admin/mesa.py", "custom_components/phoenix_mcp/mesa_core/portability.py"] }
---
Every MESA profile, whatever it covers, in one list. The list updates every 30 seconds while it's shown.

## {{label:mesa.addProfile}} {#add}

Starts a new profile in [{{label:workspace.profileEditor}}](guide:window.mesaEditor). Select what it covers: an entity, a device, an area, an integration, or a domain. Nothing is saved until you select **{{label:mesa.save}}** there.

Related: [Levels](guide:topic.mesaRules#levels)

## {{label:logs.filters}} {#filters}

Narrows the list by **{{label:mesa.colAppliesTo}}**, **{{label:mesa.colControl}}**, and **{{label:mesa.fieldEnforcement}}**. Each choice shows how many profiles it matches. **{{label:mesa.modeInherited}}** means the profile leaves control mode unset.

Search matches the ID, name, type, and tags, and keeps working while the filters are hidden. Hiding the filters stops them applying until you show them again. Changing a filter closes the open profile.

## The list {#list}

Select a profile to open it in [{{label:workspace.profileEditor}}](guide:window.mesaEditor). When the profile is enforced, {{label:mesa.modeEnforced}} shows beside its control mode.

**{{label:mesa.colTags}}** and **{{label:mesa.colSource}}** are hidden at first. Show them from a column's menu. The {{label:mesa.colSource}} column says {{label:mesa.vendorBadge}} for a profile that an integration supplies with its code.

## {{label:mesa.delete}} {#delete}

Removes a profile. Select **{{label:mesa.delete}}**, then **{{label:common.confirm}}**, or click anywhere else to back out. What it covered then follows its other profiles. If none is left, it follows the built-in baseline.

An integration's own vendor profile comes back the next time Home Assistant starts, unless you've saved your own profile for that integration.

## {{label:mesa.menu}} {#actions}

The menu holds:

- [{{label:mesa.importTitle}}](guide:#import) and [{{label:mesa.exportTitle}}](guide:#export)
- **{{label:mesa.suggestTitle}}**, which shows or hides that window
- **{{label:mesa.rescan}}**, which looks for new [suggestions](guide:window.mesaSuggestions) and {{label:workspace.advisory}} findings straight away
- **{{label:mesa.restoreDismissed}}**, which brings back every dismissed suggestion for every administrator
- **{{label:mesa.restoreNotices}}**, which brings back the findings you dismissed in [{{label:workspace.advisory}}](guide:window.mesaAdvisory), in this browser only

## {{label:mesa.importTitle}} {#import}

Adds profiles from a MESA archive file, such as one you exported. Select **{{label:mesa.browse}}** and select the file. Phoenix shows what the archive contains before you import.

Profiles that already exist here are left as they are. Turn on **{{label:mesa.replaceExisting}}** to overwrite them instead. The button turns red and reads **{{label:mesa.importAndReplace}}**. There's no further confirmation.

Imported profiles take effect straight away. Phoenix checks each one and lists any it refused. A profile for a target this home doesn't have shows up in [{{label:workspace.advisory}}](guide:window.mesaAdvisory#orphans).

An archive can hold fields {{label:workspace.profileEditor}} can't set, such as declared limits and time windows, and deployment defaults.

Related: [What {{label:workspace.profileEditor}} can't set](guide:topic.mesaRules#not-in-editor)

## {{label:mesa.exportTitle}} {#export}

Downloads every profile as one archive file, with deployment defaults if you have any. Select **{{label:mesa.export}}** to confirm. The file holds your whole agent safety policy, so keep it somewhere safe.
