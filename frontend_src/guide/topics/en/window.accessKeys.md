---
{ "id": "window.accessKeys", "title": "{{label:shell.tabAccessKeys}}", "parent": "workspace.accessKeys", "order": 1, "keywords": ["access key", "token", "bearer", "secret"], "sectionKeywords": { "create-key": ["new key", "add key"], "key-expiry": ["expire", "expiration"], "rotate": ["new secret", "leaked", "lost key"], "revoke": ["disable", "remove key", "delete key"], "reconnect": ["tools changed"] }, "covers": ["frontend_src/views/AccessKeyList.tsx", "frontend_src/components/AccessKeyCreate.tsx", "frontend_src/components/AccessKeyStatus.tsx", "frontend_src/views/AccessKeyDetail.tsx"] }
---
Your access keys. Select a key to show its settings in the other windows. Until you select one, only this list is shown.

## The list {#key-list}

Each row shows the key's name, mode, and status, and when it was created, last changed, expires and was last used. Revoked and expired keys move to [{{label:accessKeys.archivedTitle}}](guide:window.archivedAccessKeys).

To find a key, enter its name or status in the filter box above the list. Sort, move, resize, and hide columns. Phoenix saves the layout with the workspace arrangement, for your Home Assistant user.

The {{label:accessKeys.colMode}} column shows {{label:accessKeys.scoped}}, or {{label:accessKeys.passThrough}} for a key that skips the permissions tree.

Related: [What pass-through opens](guide:topic.passThrough)

## {{label:accessKeys.createAccessKey}} {#create-key}

Opens a short form above the list. Select it again on the toolbar to close the form.

### {{label:accessKeys.nameLabel}} {#key-name}

3 to 32 letters, digits, underscores, or hyphens. Each name must be unique, ignoring case, and a hyphen counts the same as an underscore. The name appears in the audit log and in approvals, so say which agent uses the key.

### {{label:accessKeys.expiryLabel}} {#key-expiry}

{{label:accessKeys.expiryNone}} (default), or a number of minutes, hours, days, or weeks. When the key expires, Phoenix archives it. You can't change the expiry after the key is created.

### Rate limits {#key-rate-limits}

Limits how fast an external app can send requests with this key. Agent Chat, the Voice Agent, Assist, and AI Task aren't limited. **{{label:accessKeys.rateRequestsShort}}** (default 60) and **{{label:accessKeys.rateBurstShort}}** (default 10). Set either to 0 for no limit. You can change both later.

Related: [{{label:accessKeys.cardRateLimiting}}](guide:window.keySettings#rate-limiting)

### {{label:accessKeys.passThroughMode}} {#key-pass-through}

Makes the key skip the permissions tree, so it can reach every entity. Use it only for an agent you fully trust. Off by default. Turn on the acknowledgement before you create it.

Related: [What pass-through opens](guide:topic.passThrough)

### {{label:oauth.switchLabel}} {#key-oauth}

Turn it on for an app that can't send an access key, such as ChatGPT or Claude. The app signs in instead and uses this key. Off by default. To turn it on later, use [{{label:workspace.keySettings}}](guide:window.keySettings#oauth-sign-in).

### After you create the key {#key-created}

Phoenix asks how you'll use the new key:

- **{{label:accessKeys.postCreateConnectTitle}}**: setup steps for your app, with the key filled in.
- **{{label:accessKeys.postCreateChatTitle}}**: opens Settings, to use the key with Agent Chat, the Voice Agent, or AI Task. The key's value isn't shown.
- **{{label:accessKeys.postCreateShowTitle}}**: the key's value, to copy.

Copy the key before you close this dialog. Phoenix stores only a hash of it and can't show it again. If you lose it, rotate the key.

## {{label:workspace.keyActions}} {#key-actions}

Opens from the toolbar and acts on the selected key. Every action except {{label:workspace.rename}} asks you to confirm, above the list.

### {{label:workspace.rename}} {#rename}

Turns the key's name into a text box. Press Enter to save, or Esc to cancel. Double-clicking the name does the same. The usual name rules apply.

### {{label:accessKeys.rotate}} {#rotate}

Gives the key a new secret value and shows it. Everything else stays the same, including its permissions and history. The old value stops working straight away, so update every app that uses it. **{{label:accessKeys.helpConnect}}** in the same dialog shows each app's setup with the new value.

Rotating cancels the approvals apps queued with the key, because whoever held the old value might have asked for them. Requests from the Voice Agent, Assist, and AI Task stay.

### {{label:accessKeys.enablePtLabel}} {#enable-pass-through}

Makes the key skip the permissions tree. The tree is kept: **{{label:accessKeys.switchToScoped}}** in the {{label:accessKeys.permissionsTree}} window turns it back on. Home Companion keys can't use pass-through.

Related: [What pass-through opens](guide:topic.passThrough)

### {{label:accessKeys.revoke}} {#revoke}

Ends the key for good. Phoenix refuses its requests and cancels its waiting approvals. Phoenix also removes it from the Voice Agent, AI Task, and Assist Tool Provider, and moves it to {{label:accessKeys.archivedTitle}}. You can't undo this. Use it for a key you no longer need. If a key leaked, rotate it instead.

## Reconnecting an agent {#reconnect}

When you change which tools a key can use, a banner above the list asks you to reconnect its agent. Some apps only read their list of tools when they connect. The banner appears only for keys that have been used.

Giving a key its first Write in the permissions tree, or removing its last one, changes its tools too, and shows the banner as well.
