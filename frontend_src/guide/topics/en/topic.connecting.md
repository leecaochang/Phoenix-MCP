---
{"id": "topic.connecting", "title": "Connecting External Apps", "parent": "topic.about", "order": 20, "keywords": ["MCP client", "connect", "setup", "external agent", "remote"], "sectionKeywords": {"methods": ["OAuth", "access key", "which method", "header"], "commands": ["Claude Code", "Gemini CLI", "Antigravity", "Codex", "Cursor", "DeepSeek Harness", "snippet", "command"], "credentials": ["secret", "leak", "protect", "bearer"], "several-keys": ["profiles", "more than one key", "server name"], "connection": ["URL", "endpoint", "Streamable HTTP", "SSE", "protocol", "session"], "reconnect": ["tools changed", "refresh tools"], "primer": ["instructions", "notes", "skill", "SKILL.md"], "oauth": ["OAuth", "ChatGPT", "Claude", "custom connector", "phone app", "hosted", "sign in"], "oauth-approve": ["approve", "deny", "sign-in request", "consent"], "ha-mcp-server": ["Home Assistant MCP Server", "/api/mcp", "Home Assistant API", "Device control"], "problems": ["401", "403", "429", "503", "unauthorized", "forbidden", "troubleshooting"], "remote": ["remote access", "Nabu Casa", "outside the home", "away from home", "VPN", "Tailscale", "reverse proxy", "https"]}, "covers": ["frontend_src/components/ConnectInstructions.tsx", "frontend_src/wizard_helpers.ts", "custom_components/phoenix_mcp/mcp_transport.py", "custom_components/phoenix_mcp/helpers.py", "custom_components/phoenix_mcp/skill_view.py", "custom_components/phoenix_mcp/assist_api.py", "frontend_src/components/OAuthRequestDialog.tsx", "custom_components/phoenix_mcp/net_guard.py"]}
---
An external app, such as a coding assistant or a desktop chat app, connects to Phoenix with one access key. The app's own model does the thinking. Phoenix decides what it can do. Apps that take only a server address [sign in with OAuth](guide:#oauth) instead of sending the key.

## Two ways to connect {#methods}

The connect instructions start with a choice:

- **{{label:wizard.methodKey}}**: the app sends the access key with every request. Use it for coding assistants and other apps that let you set a header.
- **{{label:wizard.methodOAuth}}**: the app takes only an address and signs in through Home Assistant. Use it for ChatGPT, Claude, and their phone apps. Nothing secret is copied.

If the key already allows OAuth sign-in, the instructions open on **{{label:wizard.methodOAuth}}**. Each method lists only the apps that support it.

## Getting the setup commands {#commands}

Phoenix shows the exact command or configuration for Claude Code, Gemini CLI, Antigravity CLI, Codex, Cursor, and DeepSeek Harness. A general one covers any other MCP app. Claude Code is on the Claude tab, and Codex on the ChatGPT tab.

The Codex setup also raises its tool timeout to 330 seconds, so a long ESPHome build or a wait for approval isn't cut off.

Under **{{label:wizard.methodOAuth}}**, Phoenix shows sign-in steps for Claude, ChatGPT, and VS Code. The Claude tab covers Claude Code too, and the ChatGPT tab covers the ChatGPT desktop app and the Codex CLI.

The instructions appear in three places:

- The **{{label:wizard.stepConnect}}** step of [{{label:accessKeys.setUpAgent}}](guide:wizard.connect)
- **{{label:accessKeys.postCreateConnectTitle}}**, right after you create a key
- **{{label:accessKeys.helpConnect}}**, after you rotate a key

The commands contain the key itself, which Phoenix shows only once. To see them again for an existing key, rotate it and update the app.

Each app's tab links to that app's own setup guide. Restart or reload the app afterwards.

## Keeping the key safe {#credentials}

The access key works like a password for everything its permissions allow. Phoenix stores only a hash of it, so it can't show it again.

Keep it out of shared files and chat logs. The setup commands for Claude Code, Gemini CLI, and Codex put the key on the command line. If others can read your shell history, clear it. If it leaks, [rotate](guide:window.accessKeys#rotate) the key: the old value stops working straight away.

## Using several keys {#several-keys}

Give each app or task its own key, with only the permissions it needs. Each key gets its own server name in the app, such as phx-home, so one app can hold several keys side by side.

Renaming a key keeps it working, but new setup commands use the new name.

## The connection {#connection}

Apps connect to your Home Assistant address followed by `/api/phoenix-mcp`. They send the key as a bearer token. The URL in the setup commands is the one your browser uses. If the app reaches Home Assistant another way, change it there.

Phoenix speaks MCP over Streamable HTTP. Older app settings that use the SSE transport won't connect. Long actions keep the connection alive with a message every 15 seconds.

## Apps that sign in with OAuth {#oauth}

ChatGPT, Claude, and their phone apps can't send an access key. They take only a server address and sign in instead. Phoenix runs its own sign-in for this, separate from Home Assistant's.

1. In the connect instructions, choose **{{label:wizard.methodOAuth}}**, or open the key's [{{label:workspace.keySettings}}](guide:window.keySettings#oauth-sign-in). Turn on **{{label:oauth.switchLabel}}**.
2. Choose your app's tab and follow its steps. Each uses the address Phoenix shows. Leave any client ID and secret empty.
3. The app opens Home Assistant in your browser. Sign in as an administrator, then approve the request in Phoenix.

Claude, ChatGPT, and VS Code can sign in this way, including Claude Code and the Codex CLI 0.148 or later. Antigravity, Cursor, Gemini CLI, and DeepSeek Harness can't. Give them the key instead.

VS Code takes at most 128 tools in one request. A key with many capabilities can offer more, so turn some Phoenix tools off in VS Code's tools picker.

The app then uses the key you approved it for, with the key's permissions, capabilities, approvals, and rate limit. Its requests show in the audit log under that key.

The app must reach Home Assistant over HTTPS from the internet, at the address Phoenix shows. A home network or VPN address won't work for hosted apps. Behind a reverse proxy or tunnel, Home Assistant must trust the proxy so it knows the request arrived over HTTPS. Plain HTTP never offers sign-in.

The app must find the sign-in from the server address itself (protected resource metadata, RFC 9728), as current MCP apps do. An app that knows only the older 2025-03-26 sign-in reaches Home Assistant's own sign-in instead. Phoenix refuses what that sign-in gives the app.

### Approving a sign-in {#oauth-approve}

Phoenix shows the app's name as the app gives it, the full address that identified it, and where it returns to. Check those match the app you just connected.

Choose the key the app gets, then select **{{label:oauth.approve}}**. Only keys with OAuth on and a sign-in period open are offered. Your browser goes back to the app. The app is now connected.

Select **{{label:oauth.deny}}** for a sign-in you didn't start. Phoenix refuses apps it can't verify. It fetches an app's description only from public internet addresses.

Only a Home Assistant administrator can approve. The app gets the key's rights, never yours. The dialog counts down the key's sign-in period. It closes only through its buttons, because a dismissed request can't be brought back. If the countdown ends, start again from the app.

## Connecting from outside your home {#remote}

Phoenix has no remote service of its own. An app outside your home reaches Phoenix the same way your browser would. It uses your Nabu Casa address, your own domain, or a VPN such as Tailscale. Change the URL in the setup commands to that address, followed by `/api/phoenix-mcp`.

Use an https address outside your home, because the key travels with every request. A reverse proxy must pass the Authorization header through, allow long responses, and pass on the original host and https. Hosted apps connect from their provider's servers, so a home network or VPN address won't work for them.

## When the tools change {#reconnect}

Some apps read the list of tools only when they connect. After you change a key's capabilities or permissions, reconnect the app to see the new list.

Phoenix also warns the agent on its next call. If its tools changed, Phoenix tells it to ask you to reconnect. If only the actions that need approval changed, Phoenix tells it that its tools are the same.

Related: [Reconnecting an agent](guide:window.accessKeys#reconnect)

## What the agent is told {#primer}

When an app connects, Phoenix sends it short instructions for working safely. They cover how approvals and dry runs work, which actions need your approval, and the key's notes. A Home Companion key gets a shorter fixed version.

Apps that support skills can also install the Phoenix MCP skill, a longer guide shown under **{{label:wizard.skillSectionTitle}}**. It's the same for everyone and holds nothing about your home.

## Through Home Assistant's MCP Server {#ha-mcp-server}

Apps that sign in to Home Assistant itself can also reach Phoenix's tools through Home Assistant's Model Context Protocol integration. For most apps, [OAuth sign-in](guide:#oauth) is simpler and keeps them to one key. Before 2026.11 it was called MCP Server. They use the key bound to [Assist Tool Provider](guide:topic.homeAssistantFeatures#assist-tool-provider).

From 2026.8, use `/api/mcp/phoenix_mcp`, which needs an administrator's sign-in. Home Assistant 2026.7 doesn't have that address, so use `/api/mcp` there.

From 2026.10, the `/api/mcp` address follows that integration's **Require an administrator account** option. New entries turn it on. Older entries keep it off. Before 2026.10 there is no such option, so anyone signed in to Home Assistant can use `/api/mcp`. Someone who isn't an administrator gets only household tools, within their own Home Assistant rights.

On that route, the key's rate limit doesn't apply, and the audit log shows the calls as Assist. The app gets Phoenix's instructions only if it asks for them.

From 2026.10, check that integration's options. A new entry has Expose all AI tools on, which serves every set of tools, Phoenix's included. Turn off **Expose all AI tools** and select **Phoenix MCP (scoped)** under **AI tools**. From 2026.11, the Read-only option leaves an app only the tools marked read-only, but it doesn't replace the key's capabilities.

An app that used Home Assistant's own Home Assistant API finds Phoenix's tool names instead of Home Assistant's. Phoenix has no tools for setting up integrations or reading the full log.

Tools from Home Assistant's other APIs don't pass through Phoenix on that route, so the key, approvals, MESA, and the audit log don't cover them. When two APIs offer the same tool name, Home Assistant adds a prefix such as `phoenix-mcp__`.

The Home Assistant set of AI tools, new in 2026.11 for administrators, reaches everything: any entity or action, templates, logs, and configuration. To keep an app within Phoenix's checks, select only **Phoenix MCP (scoped)**.

## When it doesn't connect {#problems}

- "Unauthorized" (401): the key is wrong, revoked, or expired, or was sent in the URL instead of the Authorization header. For an app that signed in, the sign-in ended: approve it again.
- "Forbidden" (403): a web page tried to connect from another site. Desktop and command-line apps aren't affected.
- "Too many requests" (429): the key hit its rate limit, or has too many connections open at once. Wait, then try again from the app.
- "Unavailable" (503): the kill switch is on, or Phoenix is still starting.
