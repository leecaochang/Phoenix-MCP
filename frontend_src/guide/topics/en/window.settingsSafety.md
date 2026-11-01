---
{ "id": "window.settingsSafety", "title": "{{label:workspace.safety}}", "parent": "workspace.settings", "order": 4, "sectionKeywords": { "kill-switch": ["emergency", "stop", "disable"], "mesa-mode": ["MESA", "advisory", "enforced", "enforcement"], "data-management": ["wipe", "delete", "reset", "erase"] }, "covers": ["frontend_src/components/KillSwitch.tsx", "frontend_src/components/WipeConfirmModal.tsx"] }
---
Stop Phoenix in an emergency, choose how strictly MESA protects your devices, and delete Phoenix's data.

## {{label:settings.killSwitchCard}} {#kill-switch}

**{{label:settings.killSwitchDisableLabel}}** immediately cuts off every way an AI can reach your home through Phoenix, with no confirmation. Turn it off to restore access. No restart is needed.

While it's on, a warning button on the toolbar leads back to this switch.

Related: [What the kill switch stops](guide:topic.killSwitch)

## {{label:settings.mesaModeLabel}} {#mesa-mode}

What MESA, Phoenix's device safety layer, does when an action breaks a profile's rules. Default: {{label:settings.mesaModeAdvisory}}.

- {{label:settings.mesaModeOff}}: MESA is skipped, and every action is allowed.
- {{label:settings.mesaModeAdvisory}}: most rules only warn. Privacy rules, read-only devices and profiles set to enforce still block.
- {{label:settings.mesaModeEnforced}}: forbidden actions are blocked, and actions that need confirmation wait for your approval.

Devices without a MESA profile follow a built-in baseline: lights are allowed, locks and alarm panels are forbidden, and everything else needs approval. Add profiles before you rely on {{label:settings.mesaModeEnforced}}.

Related: [MESA](guide:workspace.mesa)

## {{label:settings.dataCard}} {#data-management}

Deletes Phoenix's data. Choose what to delete, select **{{label:settings.wipeSubmit}}**, then type WIPE to confirm. The page reloads afterwards.

You can't undo a wipe. It's refused while an approved request is still running.

### {{label:settings.wipeCoreLabel}} {#wipe-core}

Deletes every access key, pending approval and entity hint, and resets all settings to their defaults. This also turns off the kill switch.

It also deletes the audit log, Conversation Log, change history, routing statistics and rate-limit counts, and removes the voice assistant Phoenix created.

### {{label:settings.wipeProvidersLabel}} {#wipe-providers}

Deletes every AI and decision provider account, with their keys. The Voice Agent, AI Task and command routing lose their accounts, and Phoenix removes the voice assistant it created.

### {{label:settings.wipeMesaLabel}} {#wipe-mesa}

Deletes every MESA profile, at every level, and the suggestions you dismissed. Off by default, so a wipe keeps your device rules unless you choose this.
