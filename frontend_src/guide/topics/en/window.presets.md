---
{ "id": "window.presets", "title": "{{label:accessKeys.cardPresets}}", "parent": "workspace.accessKeys", "order": 7, "keywords": ["snapshot", "profile", "switch setup"], "covers": ["frontend_src/views/AccessKeyDetail.tsx"] }
---
A preset is a saved setup for this key. Switch between presets to change the key quickly, for example a careful everyday preset and a broader one for maintenance. This window appears when **{{label:settings.presetsLabel}}** is on in [{{label:workspace.general}}](guide:window.settingsGeneral#presets).

A preset holds the key's persona, capabilities, permissions tree, pass-through, Assist exposure, tool announcement, approval wait and rate limits. It doesn't hold the key's name or guidance.

Each key can have up to 8 presets. The one in use is marked **{{label:accessKeys.presetActive}}**. **{{label:accessKeys.presetModified}}** means the key has changed since that preset was last saved.

## {{label:accessKeys.presetSwitchTo}} {#switch-to}

Saves the key's current settings into the active preset, then applies the one you chose. A list of what changes shows first, for you to confirm. If the preset turns on pass-through, confirm that too.

Switching tells connected agents that their tools changed, and restarts the key's rate limit count.

## {{label:accessKeys.presetRevertToSaved}} {#revert}

Throws away the changes made since the active preset was saved, after you confirm.

## {{label:accessKeys.presetSaveCurrent}} {#save-current}

Saves the key's current settings as a new preset and makes it the active one. Type a name of up to 40 characters first. The preset you were on keeps its last saved state.

## Renaming and deleting {#rename-delete}

Edit a preset's name in place; press Enter to save or Esc to cancel. **{{label:accessKeys.delete}}** removes a preset after you confirm. You can't delete the active preset.
