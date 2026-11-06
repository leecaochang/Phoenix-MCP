---
{ "id": "topic.security", "title": "Security", "parent": "topic.about", "order": 40, "keywords": ["security", "safety", "protect", "threat", "secrets", "privacy"], "sectionKeywords": { "keys": ["phx_", "bearer", "hash", "fingerprint", "query string", "Origin", "403", "DNS rebinding"], "hidden": ["scrubbing", "redacted", "sensitive attributes", "password", "token", "camera stream", "entity_picture"], "gaps": ["secret under another name", "inline password", "logs", "free text"], "untrusted": ["prompt injection", "device name", "instructions", "untrusted data"], "request-limits": ["413", "body size", "batch", "rate limit", "429", "Retry-After"], "admin": ["administrator", "long-lived access token", "admin API", "Home Assistant MCP Server"], "stored": ["backups", ".storage", "API keys", "encryption", "conversations"], "checklist": ["best practice", "recommendations", "hardening"], "emergency": ["leaked key", "compromised", "kill switch", "rotate"], "removing": ["uninstall", "wipe", "delete everything"], "leaves-home": ["cloud", "provider", "privacy", "data sent", "tool results", "Ollama", "local"] }, "covers": ["custom_components/phoenix_mcp/capability.py", "custom_components/phoenix_mcp/redaction.py", "custom_components/phoenix_mcp/policy_engine.py", "custom_components/phoenix_mcp/access_key_store.py", "custom_components/phoenix_mcp/access_key_auth.py", "custom_components/phoenix_mcp/mcp_transport.py", "custom_components/phoenix_mcp/tool_dispatch.py", "custom_components/phoenix_mcp/tool_catalog.py", "custom_components/phoenix_mcp/admin/permissions.py", "custom_components/phoenix_mcp/const.py", "custom_components/phoenix_mcp/rate_limiter.py"] }
---
Phoenix lets AI agents work in your home, so it's built to give each one only what you allow and to keep secrets out of reach. This page explains what Phoenix protects, what it can't, and what you can do to stay safe.

Related: [What every action goes through](guide:topic.about#checks), [What Agents Can Do](guide:topic.tools)

## Access keys {#keys}

Each app connects with its own access key. Phoenix stores only a fingerprint of it, so the key itself is shown once, when you create or rotate it.

- An app must send the key in its Authorization header. A key placed in a web address is refused.
- The MCP connection refuses a browser request from a page outside Home Assistant, so a malicious website can't reach it through your browser.
- A revoked or expired key stops working straight away. Requests already running stop at their next check.

Related: [Keeping the key safe](guide:topic.connecting#credentials), [{{label:accessKeys.rotate}}](guide:window.accessKeys#rotate), [{{label:accessKeys.revoke}}](guide:window.accessKeys#revoke)

## What agents never see {#hidden}

Phoenix removes some data before an agent sees it, whatever the key allows, pass-through included:

- Phoenix's own entities and the sensors it creates for each key.
- Camera stream and picture addresses, and access tokens.
- Any attribute whose name suggests a secret. That means a name containing password, pwd, token, secret, session, passphrase, api_key, wifi_key, encryption_key or license_key. It also means a name ending in pin, psk or pass.

Entities outside the key's scope look as if they don't exist. In action results, attributes, logs and dashboards they show as `<redacted>`.

## What Phoenix can't catch {#gaps}

Removing secrets works by name, so a secret under an ordinary name gets through, even one called just `key`. Keep passwords and keys in secrets.yaml, which no agent can read.

- YAML configuration hides values under names that suggest a secret, wherever they sit in the file, and comments that mention one. A password under an ordinary name is visible to the agent.
- Logs are free text. Phoenix removes access keys, tokens and passwords in web addresses, but other secrets in a log line can slip through.

Give these capabilities only to agents that need them.

Related: [Files and YAML](guide:topic.tools#files), [Configuration, templates and logs](guide:topic.tools#configuration)

## Home data is untrusted {#untrusted}

A device name or a state can contain text written by anyone, such as "ignore your instructions". When Phoenix describes the home to a model, it labels the data as untrusted content, not instructions.

That helps, but no model is perfect. Keep changes you care about on Confirm, so you see each one before it runs.

Related: [{{label:caps.modeConfirm}}](guide:window.capabilities#modes)

## Request limits {#request-limits}

- A request body can be up to 1 MB, and a batch up to 50 requests. Each request in a batch counts toward the rate limit.
- External apps are rate limited per key, by default 60 requests a minute and 10 a second. Agent Chat, Voice, Assist and AI Task aren't.
- A key over its limit is told when to try again.

Related: [{{label:accessKeys.cardRateLimiting}}](guide:window.keySettings#rate-limiting)

## The administrator side {#admin}

The panel and its settings need a Home Assistant administrator. Access keys can't open them. Any administrator credential works, including an administrator's long-lived access token, and it can create keys with any capability.

Keep the number of administrators small, and protect their tokens. Home Assistant's MCP Server reaches Phoenix without Phoenix's rate limit. Whether it needs an administrator depends on Home Assistant's version and that integration's options. Anyone else who gets through has only household tools and their own Home Assistant rights. Bind a narrow key to it.

Related: [Through Home Assistant's MCP Server](guide:topic.connecting#ha-mcp-server)

## What's stored where {#stored}

Phoenix keeps its data in Home Assistant's storage folder. Access keys are stored only as fingerprints. AI provider API keys and recorded conversations are kept there as they are, not encrypted.

Home Assistant backups include that folder. Treat backups and the configuration folder as secret.

Related: [What the Logs Record](guide:topic.logRecords)

## What leaves your home {#leaves-home}

Phoenix runs inside Home Assistant, but your AI model may not. Whatever Phoenix sends to a cloud provider leaves your home, and that provider's terms apply to it.

- Agent Chat sends the conversation with every message, including images you attach.
- Tool results go too: states, names, history, logs, YAML files and camera images, whatever the access key lets the agent read.
- The Voice Agent, AI Task and Home Companion send the same kinds of data to their accounts.
- A decision provider receives what was said. For some checks it also gets recent messages and lists of devices or tools to choose from.
- An external app sends what it reads to its own model. That app's settings decide where it goes.

To keep everything at home, use a local model on Ollama and no decision provider. With a cloud provider, give a key only the camera, log and YAML access you're happy to share.

Related: [{{label:workspace.aiProviders}}](guide:window.settingsProviders)

## Keeping it safe {#checklist}

- Give each app its own key, with only the capabilities it needs.
- Use pass-through only for an agent you fully trust. It also opens Diagnostics and Service response data.
- Keep System log read, Diagnostics and YAML configuration for agents that need them.
- Before setting an expiry on a key used by Voice, AI Task or Assist, know that expiry unlinks it.
- Use HTTPS when apps connect over a network you don't trust. The key travels with every request.

Related: [What Pass-Through Opens](guide:topic.passThrough)

## If something goes wrong {#emergency}

If a key leaks, rotate it. The old value stops working at once, and the app needs the new one. Revoke a key you no longer use.

If an agent misbehaves, turn on the [{{label:settings.killSwitchCard}}](guide:topic.killSwitch). It refuses new agent work and stops running requests at their next check. It can't undo actions already sent, so check [{{label:attention.title}}](guide:window.attention) afterwards.

## Removing Phoenix completely {#removing}

Deleting the integration revokes the keys but keeps settings, logs and archived keys. To remove Phoenix's data too, use **{{label:settings.wipeCoreLabel}}** first.

That leaves safety copies, the calendar change journal, dashboard resource records and, unless you choose [{{label:settings.wipeMesaLabel}}](guide:window.settingsSafety#wipe-mesa), MESA profiles. After you delete the integration, delete any remaining files that start with `phoenix_mcp` in the `.storage` folder.

Related: [Removing Phoenix MCP](guide:topic.install#removing), [{{label:settings.wipeCoreLabel}}](guide:window.settingsSafety#wipe-core)
