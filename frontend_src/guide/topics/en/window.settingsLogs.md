---
{ "id": "window.settingsLogs", "title": "{{label:workspace.logsNotifications}}", "parent": "workspace.settings", "order": 5, "keywords": ["logging", "record"], "sectionKeywords": { "audit-log": ["audit"], "log-max-entries": ["retention"], "conversation-log": ["history", "retention"], "privacy": ["redact"], "log-client-ip": ["IP address"], "notifications": ["notification", "alert"] }, "covers": ["frontend_src/components/LoggingSettings.tsx", "frontend_src/components/ConversationSettings.tsx", "frontend_src/components/NotificationSettings.tsx"] }
---
Choose what Phoenix records, how long it keeps it, and when Home Assistant notifies you. This window also opens from **{{label:logs.logSettings}}** in the {{label:workspace.logs}} workspace.

## {{label:shell.tabAudit}} {#audit-log}

The audit log records every request Phoenix receives, and its outcome.

### {{label:settings.logDisableAllLabel}} {#log-disable-all}

Stops all recording: no audit entries and no conversations. The other logging settings are greyed out. Off by default.

Phoenix's sensors keep counting, and notifications still arrive.

### {{label:settings.logAllowedLabel}} {#log-allowed}

Records requests that were carried out. On by default.

### {{label:settings.logDeniedLabel}} {#log-denied}

Records requests that were refused, malformed, or named something that doesn't exist. On by default. Requests waiting for approval are always recorded.

### {{label:settings.logRateLimitedLabel}} {#log-rate-limited}

Records requests turned away because a key sent too many. On by default.

These three switches affect new entries only. Existing entries stay.

### {{label:settings.flushIntervalLabel}} {#log-flush}

How often the audit log is saved to disk: every 5, 10, 15 (default), 30 or 60 minutes, or never. It's also saved when Home Assistant stops or Phoenix reloads.

### {{label:settings.maxLogEntriesLabel}} {#log-max-entries}

How many entries the audit log keeps: 100, 1,000, 5,000 or 10,000 (default). Lowering it removes the oldest entries straight away. The copy on disk shrinks at the next save.

## {{label:conversations.title}} {#conversation-log}

**{{label:conversations.enable}}** keeps a record of Agent Chat and voice conversations, which you can review in the {{label:workspace.logs}} workspace. Off by default. Turning it off keeps what was already recorded.

Three limits control how much is kept. Each one saves a moment after you change it. When the log goes over a limit, the oldest conversations are removed first.

### {{label:conversations.limit.entries}} {#conversation-entries}

How many conversations to keep: 1 to 2,000 (default 100).

### {{label:conversations.limit.days}} {#conversation-days}

How many days to keep a conversation: 1 to 365 (default 7).

### {{label:conversations.limit.mb}} {#conversation-size}

How much space the log can use: 1 to 250 MB (default 25). The space used now is shown below the limits.

## {{label:settings.privacySection}} {#privacy}

Choose what the logs show about your home and the people using Phoenix.

### {{label:settings.logEntityIdsLabel}} {#log-entity-ids}

When off, the audit log hides which device or entity a request named, and the Conversation Log leaves out what was said. On by default.

### {{label:settings.logClientIpLabel}} {#log-client-ip}

When off, both logs hide the caller's IP address. On by default.

## {{label:settings.haNotifications}} {#notifications}

Phoenix can send Home Assistant notifications, which appear in the Home Assistant sidebar.

### {{label:settings.notifyApprovalLabel}} {#notify-approval}

Sends a notification with a link to each request waiting for your approval. It goes away once you decide. On by default. The Approvals count on the toolbar shows either way.

### {{label:settings.notifyRateLimitLabel}} {#notify-rate-limit}

Notifies you when an access key sends requests faster than its limit allows. Off by default. Each key has one notification, updated at most once a minute.
