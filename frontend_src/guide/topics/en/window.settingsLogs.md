---
{ "id": "window.settingsLogs", "title": "{{label:workspace.logsNotifications}}", "parent": "workspace.settings", "order": 5, "keywords": ["logging", "record"], "sectionKeywords": { "audit-log": ["audit"], "log-max-entries": ["retention"], "conversation-log": ["history", "retention"], "privacy": ["redact"], "log-client-ip": ["IP address"], "notifications": ["notification", "alert"] }, "covers": ["frontend_src/components/LoggingSettings.tsx", "frontend_src/components/ConversationSettings.tsx", "frontend_src/components/NotificationSettings.tsx"] }
---
Choose what Phoenix records, how long it keeps it, and when Home Assistant notifies you. This window also opens from **{{label:logs.logSettings}}** in the {{label:workspace.logs}} workspace.

## {{label:shell.tabAudit}} {#audit-log}

The audit log records what agents do through Phoenix and what administrators change, with each outcome.

Related: [What the logs record](guide:topic.logRecords)

### {{label:settings.logEnableLabel}} {#log-enable}

Records audit entries and conversations. Turn it off to stop all recording. The other logging settings are then greyed out. On by default.

Phoenix's sensors keep counting, and notifications still arrive.

### {{label:settings.logAllowedLabel}} {#log-allowed}

Records requests that were carried out. On by default.

### {{label:settings.logDeniedLabel}} {#log-denied}

Records requests that were refused, malformed, not supported, or named something that doesn't exist. MESA blocks count here too. On by default.

While **{{label:settings.logEnableLabel}}** is on, Phoenix always records requests waiting for approval, your approval decisions, and requests cut short while a change might have run.

A key's first 30 malformed, unsupported, or rate-limited requests in a minute each get an entry. The rest of that minute share one entry, which counts them in its payload.

Requests refused by permissions or MESA, or naming something that doesn't exist, always get their own entry. So do writes that might have run.

### {{label:settings.logRateLimitedLabel}} {#log-rate-limited}

Records requests turned away because a key sent too many. On by default.

These three switches affect new entries only. Existing entries stay.

### {{label:settings.flushIntervalLabel}} {#log-flush}

How often the audit log is saved to disk: every 5, 10, 15 (default), 30 or 60 minutes, or never. It's also saved when Home Assistant stops or Phoenix reloads.

### {{label:settings.maxLogEntriesLabel}} {#log-max-entries}

How many entries the audit log keeps: 100, 1,000, 5,000, or 10,000 (default). Lowering it removes the oldest entries straight away. The copy on disk shrinks at the next save.

Phoenix keeps approval decisions and results, and administrator entries, in a separate list of the same size. Agents' requests never push them out. A request waiting for approval, and its expiry, stay with the key's other requests.

## {{label:conversations.title}} {#conversation-log}

**{{label:conversations.enable}}** keeps a record of what agents say and do. It covers Agent Chat and voice conversations, AI Task, and the tool calls of MCP clients, Assist, and {{label:personas.home_companion.label}}. Review it in the {{label:workspace.logs}} workspace. Off by default. Turning it off keeps what was already recorded.

Three limits control how much is kept. Each one saves a moment after you change it. When the log goes over a limit, the oldest conversations are removed first.

### {{label:conversations.limit.entries}} {#conversation-entries}

How many conversations to keep: 1 to 2,000 (default 100).

### {{label:conversations.limit.days}} {#conversation-days}

How many days to keep a conversation: 1 to 365 (default 7).

### {{label:conversations.limit.mb}} {#conversation-size}

How much space the log can use: 1 to 250 MB (default 25). The space used now is shown below the limits. A large log takes longer to load when Home Assistant starts, and can use about twice this size in memory.

## {{label:settings.privacySection}} {#privacy}

Choose what the logs show about your home and the people using Phoenix.

### {{label:settings.logEntityIdsLabel}} {#log-entity-ids}

When off, the audit log's {{label:audit.rowResource}} column shows [redacted], and {{label:conversations.title}} leaves out what was said and done. On by default.

The audit log's payload then keeps only the names of a tool call's arguments, not their values. Other payload text, such as an approval's reason, can still name an entity. Conversation content already recorded is removed the next time Phoenix starts.

### {{label:settings.logClientIpLabel}} {#log-client-ip}

When off, both logs hide the caller's IP address. An address written into a tool call's arguments stays in the audit payload. On by default. Agent Chat, voice, Assist, AI Task, and {{label:personas.home_companion.label}} still show by name, since they aren't addresses.

## {{label:settings.haNotifications}} {#notifications}

Phoenix can send Home Assistant notifications, which appear in the Home Assistant sidebar.

### {{label:settings.notifyApprovalLabel}} {#notify-approval}

Sends a notification with a link to each request waiting for your approval. It goes away once you decide. On by default. The {{label:shell.tabApprovals}} count on the toolbar shows either way.

### {{label:settings.notifyRateLimitLabel}} {#notify-rate-limit}

Notifies you when an access key sends requests faster than its limit allows. Off by default. Each key has one notification, updated at most once a minute.
