---
{ "id": "window.mesaEditor", "title": "{{label:workspace.profileEditor}}", "parent": "workspace.mesa", "order": 2, "keywords": ["MESA", "edit profile"], "sectionKeywords": { "target": ["entity", "device", "area", "integration", "domain"], "tags": ["semantic tags", "capability tags"], "control-mode": ["autonomous", "confirm", "read-only", "prohibited", "unset", "approval"], "enforcement": ["advisory", "enforced"], "triggers-automations": ["likely", "trigger validation"], "effect": ["reversible", "reversibility cost", "side-effect scope"], "privacy-level": ["public", "normal", "sensitive", "restricted"], "save": ["discard", "unsaved"], "effective-resolution": ["inherited", "origin", "from", "resolution warnings"], "from-home-assistant": ["dialog", "in-context", "plus button"] }, "covers": ["frontend_src/views/MesaView.tsx", "frontend_src/views/MesaWindows.tsx", "frontend_src/components/TagInput.tsx", "frontend_src/inject/QuickAdd.tsx", "custom_components/phoenix_mcp/admin/mesa.py"] }
---
The profile you chose in [{{label:workspace.mesaProfiles}}](guide:window.mesaProfiles), or a new one. The title says what kind of profile it is and what it covers.

## What it covers {#target}

For a new profile, select what it covers from the list. Enter part of its name or ID to narrow the list. Capitals and accents don't matter. Screen readers announce how many match. The list leaves out targets that already have a profile of this kind. Once a profile is saved, its target can't change.

## {{label:mesa.semanticTags}} {#tags}

Standard MESA tags that tell agents what the device is and does, such as a door lock or a heater. Tags don't change what agents can do. Add up to 6. **{{label:mesa.showSuggestions}}** lists tags that suit the domain.

## {{label:mesa.fieldControlMode}} {#control-mode}

How agents can change what the profile covers:

- **{{label:mesa.modeAutonomous}}**: agents act freely.
- **{{label:mesa.modeConfirm}}**: use it to approve each action yourself. Actions wait for your approval only while MESA is enforced.
- **{{label:mesa.modeReadOnly}}**: agents can't change it, in any MESA mode except {{label:settings.mesaModeOff}}.
- **{{label:mesa.modeProhibited}}**: agents can't act on it while MESA is enforced. In {{label:settings.mesaModeAdvisory}} mode it only warns them.
- **{{label:mesa.unset}}**: leaves the decision to other profiles or the built-in baseline. The list shows **{{label:mesa.modeInherited}}**.

A new entity profile starts at the mode the entity has now. Other new profiles start at {{label:mesa.unset}}.

Setting a mode replaces the built-in baseline, even when it's looser. For example, a lock domain profile set to {{label:mesa.modeConfirm}} makes every lock {{label:mesa.modeConfirm}} instead of {{label:mesa.modeProhibited}}.

Related: [How profiles combine](guide:topic.mesaRules#combining), [The built-in baseline](guide:topic.mesaRules#baseline)

## {{label:mesa.fieldEnforcement}} {#enforcement}

To make one device strict while **{{label:settings.mesaModeLabel}}** in Settings stays {{label:settings.mesaModeAdvisory}}, set this to **{{label:mesa.modeEnforced}}**. While the setting is {{label:settings.mesaModeOff}}, nothing is enforced.

**{{label:mesa.modeAdvisory}}** (default) leaves it to the setting. A broader profile that's {{label:mesa.modeEnforced}} still applies.

Related: [What happens when an action breaks a rule](guide:topic.mesaRules#modes)

## {{label:mesa.fieldTriggersAutomations}} {#triggers-automations}

Whether changing this is likely to start automations. MESA checks your automations against it. If it's marked **{{label:mesa.trigNone}}** and an automation still uses it, MESA shows a finding in [{{label:workspace.advisory}}](guide:window.mesaAdvisory#validation). Default: **{{label:mesa.trigUnknown}}**.

## Describing the effect {#effect}

**{{label:mesa.fieldReversible}}**, **{{label:mesa.fieldReversibilityCost}}**, and **{{label:mesa.fieldSideEffectScope}}** tell agents whether a change can be undone, what undoing it costs, and how far its effect reaches. They inform agents but don't block anything. Each is {{label:mesa.unset}} by default.

## {{label:mesa.fieldPrivacyLevel}} {#privacy-level}

How sensitive the data is that this exposes to agents. Default: **{{label:mesa.privNormal}}**. **{{label:mesa.privRestricted}}** makes an {{label:mesa.modeAutonomous}} profile need confirmation.

Related: [Privacy](guide:topic.mesaRules#privacy)

## Saving {#save}

**{{label:mesa.save}}** stores the profile. It takes effect straight away. For an existing profile, {{label:mesa.save}} is available once you've changed something.

**{{label:mesa.discardChanges}}** puts the form back as it was saved, or closes a new profile. Selecting another profile in the list also replaces the form, so you lose unsaved changes.

Saving replaces the whole profile with what the form shows. Fields the form doesn't show, such as declared limits or time windows from an import, are removed. A vendor profile becomes your own.

Related: [What {{label:workspace.profileEditor}} can't set](guide:topic.mesaRules#not-in-editor)

### Trigger warnings {#save-warnings}

After you save an entity profile, Phoenix checks your automations against it. If it finds a problem, the profile is saved and the warnings are listed under the form. Select **{{label:mesa.dismiss}}** to close them.

### {{label:mesa.delete}} {#delete}

Removes the profile after you confirm in place. What it covered then follows its other profiles, or the built-in baseline.

## {{label:mesa.effectiveResolution}} {#effective-resolution}

For a saved entity profile, the values MESA uses once every level is combined. Check it when a setting doesn't behave as the profile says. **{{label:mesa.colFrom}}** names the level each value comes from. **{{label:mesa.colOrigin}}** says who wrote it: user for you, developer for an integration's vendor profile.

**{{label:mesa.resolutionWarnings}}** above it means a rule didn't take effect as written, such as an override that MESA ignored. The warnings come from MESA itself and are in English.

## From Home Assistant's pages {#from-home-assistant}

With profile buttons turned on, the same editor opens as a dialog from Home Assistant's own pages. It opens for the entity, device, area, or integration you're looking at. {{label:mesa.cancel}} asks before it discards your changes.

Related: [{{label:settings.injectLabel}}](guide:window.settingsGeneral#inject)
