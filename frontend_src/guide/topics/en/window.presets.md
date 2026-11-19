---
{ "id": "window.presets", "title": "{{label:accessKeys.cardPresets}}", "parent": "workspace.accessKeys", "order": 7, "keywords": ["snapshot", "profile", "switch setup"], "covers": ["frontend_src/views/AccessKeyDetail.tsx"] }
---
A preset is a saved setup for this key. Switch between presets to change the key quickly, for example a careful everyday preset and a broader one for maintenance. To show this window, turn on **{{label:settings.presetsLabel}}** in [{{label:workspace.general}}](guide:window.settingsGeneral#presets).

A preset holds the key's persona, capabilities, permissions tree, pass-through, Assist exposure, tool announcement, approval wait, and rate limits. It doesn't hold the key's name or guidance.

Each key can have up to 8 presets. The preset in use shows {{label:accessKeys.presetActive}}. {{label:accessKeys.presetModified}} means the key has changed since that preset was last saved.

## {{label:accessKeys.presetSwitchTo}} {#switch-to}

Applies the preset you selected. First, Phoenix saves the key's current settings into the active preset. To discard them instead, use **{{label:accessKeys.presetRevertToSaved}}** before you switch. A list of what changes shows first, for you to confirm. If the preset turns on pass-through, confirm that too.

Switching tells connected agents that their tools changed, and restarts the key's rate limit count.

## {{label:accessKeys.presetRevertToSaved}} {#revert}

Throws away the changes made since the active preset was saved, after you confirm. Use it before **{{label:accessKeys.presetSwitchTo}}** to keep those changes out of the active preset.

## {{label:accessKeys.presetSaveCurrent}} {#save-current}

Saves the key's current settings as a new preset and makes it the active one. First, enter a name of up to 40 characters. The preset you were on keeps its last saved state.

## Renaming and deleting {#rename-delete}

Edit a preset's name in place. Press Enter to save or Esc to cancel. **{{label:accessKeys.delete}}** removes a preset after you confirm. You can't delete the active preset.
