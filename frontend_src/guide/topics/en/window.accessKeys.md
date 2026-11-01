---
{ "id": "window.accessKeys", "title": "{{label:shell.tabAccessKeys}}", "parent": "workspace.accessKeys", "order": 1, "keywords": ["access key", "token", "bearer", "secret"], "sectionKeywords": { "create-key": ["new key", "add key"], "key-expiry": ["expire", "expiration"], "rotate": ["new secret", "leaked", "lost key"], "revoke": ["disable", "remove key", "delete key"], "reconnect": ["tools changed"] }, "covers": ["frontend_src/views/AccessKeyList.tsx", "frontend_src/components/AccessKeyCreate.tsx", "frontend_src/components/AccessKeyStatus.tsx", "frontend_src/views/AccessKeyDetail.tsx"] }
---
Your access keys. Select a key to show its settings in the other windows. Until you select one, only this list is shown.

## The list {#key-list}

Each row shows the key's name, mode and status, and when it was created, last changed, expires and was last used. Revoked and expired keys move to [{{label:accessKeys.archivedTitle}}](guide:window.archivedAccessKeys).

Type in the filter box above the list to find a key by name or status. You can sort, move, resize and hide columns; the layout is kept in this browser.

The Mode column shows Scoped, or Pass Through for a key that skips the permissions tree.

Related: [What pass-through opens](guide:topic.passThrough)

## {{label:accessKeys.createAccessKey}} {#create-key}

**{{label:accessKeys.createAccessKey}}** on the toolbar opens a short form above the list. Select it again to close the form.

### {{label:accessKeys.nameLabel}} {#key-name}

3 to 32 letters, digits, underscores or hyphens. Each name must be unique, ignoring case, and a hyphen counts the same as an underscore. The name appears in the audit log and in approvals, so say which agent uses the key.

### {{label:accessKeys.expiryLabel}} {#key-expiry}

No Expiry (default), or a number of minutes, hours, days or weeks. When the key expires, Phoenix archives it. You can't change the expiry after the key is created.

### Rate limits {#key-rate-limits}

**{{label:accessKeys.rateRequestsLabel}}** (default 60) and **{{label:accessKeys.rateBurstLabel}}** (default 10). You can change both later.

Related: [Rate limiting](guide:window.keySettings#rate-limiting)

### {{label:accessKeys.passThroughMode}} {#key-pass-through}

Makes the key skip the permissions tree, so it can reach every entity. Off by default. Turn on the acknowledgement before you create it.

Related: [What pass-through opens](guide:topic.passThrough)

### After you create the key {#key-created}

Phoenix asks how you'll use the new key:

- **{{label:accessKeys.postCreateConnectTitle}}**: setup steps for your app, with the key filled in.
- **{{label:accessKeys.postCreateChatTitle}}**: opens Settings, to use the key with Agent Chat, the Voice Agent or AI Task. The key's value isn't shown.
- **{{label:accessKeys.postCreateShowTitle}}**: the key's value, to copy.

Copy the key before you close this dialog. Phoenix stores only a hash of it and can't show it again. If you lose it, rotate the key.

## {{label:workspace.keyActions}} {#key-actions}

**{{label:workspace.keyActions}}** on the toolbar acts on the selected key. Every action except Rename asks you to confirm, above the list.

### {{label:workspace.rename}} {#rename}

Turns the key's name into a text box. Press Enter to save, or Esc to cancel. You can also double-click the name. The usual name rules apply.

### {{label:accessKeys.rotate}} {#rotate}

Gives the key a new secret value and shows it. Everything else stays the same, including its permissions and history. The old value stops working straight away, so update every app that uses it. **{{label:accessKeys.helpConnect}}** in the same dialog shows each app's setup with the new value.

### {{label:accessKeys.enablePtLabel}} {#enable-pass-through}

Makes the key skip the permissions tree. The tree is kept: **{{label:accessKeys.switchToScoped}}** in the {{label:accessKeys.permissionsTree}} window turns it back on. Home Companion keys can't use pass-through.

Related: [What pass-through opens](guide:topic.passThrough)

### {{label:accessKeys.revoke}} {#revoke}

Ends the key for good. Its requests are refused, and its waiting approvals are cancelled. Phoenix also removes it from the Voice Agent, AI Task and Assist Tool Provider, and moves it to {{label:accessKeys.archivedTitle}}. You can't undo this.

## Reconnecting an agent {#reconnect}

When you change which tools a key may use, a banner above the list asks you to reconnect its agent. Some apps only read their list of tools when they connect. The banner appears only for keys that have been used.

Giving a key write access to something it couldn't change before, or taking it away, changes its tools too, but shows no banner. Reconnect the app then as well.
