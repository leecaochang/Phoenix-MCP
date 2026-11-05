---
{"id": "topic.connecting", "title": "Connecting External Apps", "parent": "topic.about", "order": 20, "keywords": ["MCP client", "connect", "setup", "external agent", "remote"], "sectionKeywords": {"commands": ["Claude Code", "Gemini CLI", "Antigravity", "Codex", "Cursor", "DeepSeek Harness", "snippet", "command"], "credentials": ["secret", "leak", "protect", "bearer"], "several-keys": ["profiles", "more than one key", "server name"], "connection": ["URL", "endpoint", "Streamable HTTP", "SSE", "protocol", "session"], "reconnect": ["tools changed", "refresh tools"], "primer": ["instructions", "notes", "skill", "SKILL.md"], "ha-mcp-server": ["Home Assistant MCP Server", "/api/mcp", "hosted", "connector"], "problems": ["401", "403", "429", "503", "unauthorized", "forbidden", "troubleshooting"], "remote": ["remote access", "Nabu Casa", "outside the home", "away from home", "VPN", "Tailscale", "reverse proxy", "https"]}, "covers": ["frontend_src/components/ConnectInstructions.tsx", "frontend_src/wizard_helpers.ts", "custom_components/phoenix_mcp/mcp_view.py", "custom_components/phoenix_mcp/helpers.py", "custom_components/phoenix_mcp/skill_view.py", "custom_components/phoenix_mcp/assist_api.py"]}
---
An external app, such as a coding assistant or a desktop chat app, connects to Phoenix with one access key. The app's own model does the thinking, and Phoenix decides what it may do.

## Getting the setup commands {#commands}

Phoenix shows the exact command or configuration for Claude Code, Gemini CLI, Antigravity CLI, Codex, Cursor and DeepSeek Harness, plus a general one for any other MCP app. They appear in three places:

- The **Connect** step of [{{label:accessKeys.setUpAgent}}](guide:wizard.connect).
- **{{label:accessKeys.postCreateConnectTitle}}**, right after you create a key.
- **{{label:accessKeys.helpConnect}}**, after you rotate a key.

The commands contain the key itself, which Phoenix shows only once. To see them again for an existing key, rotate it and update the app.

Each app's tab links to that app's own setup guide. Restart or reload the app afterwards.

## Keeping the key safe {#credentials}

The access key works like a password for everything its permissions allow. Phoenix stores only a fingerprint of it, so it can't show it again.

Keep it out of shared files and chat logs. The setup commands for Claude Code, Gemini and Codex put it on the command line, so clear your shell history if others can read it. If it leaks, [rotate](guide:window.accessKeys#rotate) the key: the old value stops working straight away.

## Using several keys {#several-keys}

Give each app or task its own key, with only the permissions it needs. Each key gets its own server name in the app, such as phx-home, so one app can hold several keys side by side.

Renaming a key keeps it working, but new setup commands use the new name.

## The connection {#connection}

Apps connect to your Home Assistant address followed by /api/phoenix-mcp, sending the key as a bearer token. The URL in the setup commands is the one your browser uses; change it there if the app reaches Home Assistant another way.

Phoenix speaks MCP over Streamable HTTP. Older app settings that use the SSE transport won't connect. Long actions keep the connection alive with a message every 15 seconds.

## Connecting from outside your home {#remote}

Phoenix has no remote service of its own. An app outside your home reaches it the same way your browser would: through your Nabu Casa address, your own domain, or a VPN such as Tailscale. Change the URL in the setup commands to that address, followed by /api/phoenix-mcp.

Use an https address outside your home, because the key travels with every request. A reverse proxy must pass the Authorization header through and allow long responses. Hosted apps connect from their provider's servers, so a home network or VPN address won't work for them.

## When the tools change {#reconnect}

Some apps read the list of tools only when they connect. After you change a key's capabilities or permissions, reconnect the app to see the new list. Phoenix also tells the agent on its next call: to reconnect when its tools changed, or, when only which actions need approval changed, that its tools are the same.

Related: [Reconnecting an agent](guide:window.accessKeys#reconnect)

## What the agent is told {#primer}

When an app connects, Phoenix sends it short instructions for working safely: how approvals and dry runs work, which actions need your approval, and the key's notes. A Home Companion key gets a shorter fixed version.

Apps that support skills can also install the Phoenix MCP skill, a longer guide shown under **{{label:wizard.skillSectionTitle}}**. It's the same for everyone and holds nothing about your home.

## Through Home Assistant's MCP Server {#ha-mcp-server}

Apps that sign in to Home Assistant itself, such as hosted connectors, can reach Phoenix's tools through Home Assistant's Model Context Protocol integration, at /api/mcp/phoenix_mcp. Before 2026.11 it was called MCP Server. They use the key bound to [Assist Tool Provider](guide:topic.homeAssistantFeatures#assist-tool-provider) and need an administrator's sign-in.

On that route the key's rate limit doesn't apply, the audit log shows the calls as Assist, and the app gets Phoenix's instructions only if it asks for them.

From 2026.11, check that integration's options. A new entry serves every API, Phoenix's included, unless you choose. The read-only option leaves an app only the tools marked read-only, but it doesn't replace the key's capabilities.

Tools from Home Assistant's other APIs don't pass through Phoenix on that route, so the key, approvals, MESA and the audit log don't cover them. When two APIs offer the same tool name, Home Assistant adds a prefix such as `phoenix-mcp__`.

## When it doesn't connect {#problems}

- **Unauthorized (401)**: the key is wrong, revoked or expired, or was sent in the URL instead of the Authorization header.
- **Forbidden (403)**: a web page tried to connect from another site. Desktop and command-line apps aren't affected.
- **Too many requests (429)**: the key hit its rate limit, or has too many connections open at once. The app should wait and try again.
- **Unavailable (503)**: the kill switch is on, or Phoenix is still starting.
