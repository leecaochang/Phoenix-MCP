---
{ "id": "window.mesaEditor", "title": "{{label:workspace.profileEditor}}", "parent": "workspace.mesa", "order": 2, "keywords": ["MESA", "edit profile"], "sectionKeywords": { "target": ["entity", "device", "area", "integration", "domain"], "tags": ["semantic tags", "capability tags"], "control-mode": ["autonomous", "confirm", "read-only", "prohibited", "unset", "approval"], "enforcement": ["advisory", "enforced"], "triggers-automations": ["likely", "trigger validation"], "effect": ["reversible", "reversibility cost", "side-effect scope"], "privacy-level": ["public", "normal", "sensitive", "restricted"], "save": ["discard", "unsaved"], "effective-resolution": ["inherited", "origin", "from", "resolution warnings"], "from-home-assistant": ["dialog", "in-context", "plus button"] }, "covers": ["frontend_src/views/MesaView.tsx", "frontend_src/views/MesaWindows.tsx", "frontend_src/components/TagInput.tsx", "frontend_src/inject/QuickAdd.tsx", "custom_components/phoenix_mcp/admin_view.py"] }
---
The profile you chose in [{{label:workspace.mesaProfiles}}](guide:window.mesaProfiles), or a new one. The title says what kind of profile it is and what it covers.

## What it covers {#target}

For a new profile, choose what it covers from the list. Targets that already have a profile of this kind aren't offered. Once a profile is saved, its target can't change.

## {{label:mesa.semanticTags}} {#tags}

Standard MESA tags that tell agents what the device is and does, such as a door lock or a heater. Tags don't change what agents may do. Add up to 6. **{{label:mesa.showSuggestions}}** lists tags that suit the domain.

## {{label:mesa.fieldControlMode}} {#control-mode}

How agents may change what the profile covers:

- **{{label:mesa.modeAutonomous}}**: agents act freely.
- **{{label:mesa.modeConfirm}}**: actions wait for your approval while MESA is enforced.
- **{{label:mesa.modeReadOnly}}**: agents can't change it, in any MESA mode except Off.
- **{{label:mesa.modeProhibited}}**: agents can't act on it while MESA is enforced. In {{label:settings.mesaModeAdvisory}} mode it only warns them.
- **{{label:mesa.unset}}**: the profile doesn't decide. Other profiles or the built-in baseline do, and the list shows **{{label:mesa.modeInherited}}**.

A new entity profile starts at the mode the entity has now. Other new profiles start at {{label:mesa.unset}}.

Setting a mode replaces the built-in baseline, even when it's looser. A Confirm lock domain profile makes every lock Confirm instead of Prohibited.

Related: [How profiles combine](guide:topic.mesaRules#combining), [The built-in baseline](guide:topic.mesaRules#baseline)

## {{label:mesa.fieldEnforcement}} {#enforcement}

**{{label:mesa.modeEnforced}}** enforces this profile while **{{label:settings.mesaModeLabel}}** in Settings is {{label:settings.mesaModeAdvisory}}. Use it for one device you want strict without making MESA strict everywhere. While the setting is Off, nothing is enforced.

**{{label:mesa.modeAdvisory}}** (default) leaves it to the setting. It isn't saved, so a broader profile that's Enforced still applies.

Related: [What happens when an action breaks a rule](guide:topic.mesaRules#modes)

## {{label:mesa.fieldTriggersAutomations}} {#triggers-automations}

Whether changing this is likely to start automations. MESA checks your automations against it: one marked **{{label:mesa.trigNone}}** that an automation still uses shows a finding in [{{label:workspace.advisory}}](guide:window.mesaAdvisory#validation). Default: **{{label:mesa.trigUnknown}}**.

## Describing the effect {#effect}

**{{label:mesa.fieldReversible}}**, **{{label:mesa.fieldReversibilityCost}}** and **{{label:mesa.fieldSideEffectScope}}** tell agents whether a change can be undone, what undoing it costs, and how far its effect reaches. They inform agents but don't block anything. Each is {{label:mesa.unset}} by default.

## {{label:mesa.fieldPrivacyLevel}} {#privacy-level}

How sensitive the data is that this exposes to agents. Default: **{{label:mesa.privNormal}}**. **{{label:mesa.privRestricted}}** makes an Autonomous profile need confirmation.

Related: [Privacy](guide:topic.mesaRules#privacy)

## Saving {#save}

**{{label:mesa.save}}** stores the profile. It takes effect straight away. For an existing profile, Save is available once you've changed something.

**{{label:mesa.discardChanges}}** puts the form back as it was saved, or closes a new profile. Choosing another profile in the list also replaces the form, so unsaved changes are lost.

Saving replaces the whole profile with what the form shows. Fields the form doesn't show, such as declared limits or time windows from an import, are removed. A vendor profile becomes your own.

Related: [What Profile Editor can't set](guide:topic.mesaRules#not-in-editor)

### Trigger warnings {#save-warnings}

After you save an entity profile, Phoenix checks your automations against it. If it finds a problem, the profile is saved and the warnings are listed under the form. Select **{{label:mesa.dismiss}}** to close them.

### {{label:mesa.delete}} {#delete}

Removes the profile after you confirm in place. What it covered then follows its other profiles, or the built-in baseline.

## {{label:mesa.effectiveResolution}} {#effective-resolution}

For a saved entity profile, the values MESA actually uses once every level is combined. **{{label:mesa.colFrom}}** names the level each value comes from. **{{label:mesa.colOrigin}}** says who wrote it: user for you, developer for an integration's vendor profile.

**{{label:mesa.resolutionWarnings}}** above it means a rule didn't take effect as written, such as an override that MESA ignored. The warnings come from MESA itself and are in English.

## From Home Assistant's pages {#from-home-assistant}

With profile buttons turned on, the same editor opens as a dialog from Home Assistant's own pages, for the entity, device, area or integration you're looking at. Cancel asks before it discards your changes.

Related: [{{label:settings.injectLabel}}](guide:window.settingsGeneral#inject)
