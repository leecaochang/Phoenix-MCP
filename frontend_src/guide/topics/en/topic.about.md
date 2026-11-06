---
{ "id": "topic.about", "title": "About Phoenix MCP", "order": 1, "keywords": ["introduction", "overview", "what is", "getting started", "MCP"], "sectionKeywords": { "ways": ["agent chat", "external app", "Claude", "ChatGPT", "client"], "checks": ["capabilities", "permissions", "MESA", "approvals", "layers"], "token": ["long-lived access token", "LLAT", "token"], "start": ["wizard", "set up an agent", "first steps"] }, "covers": ["custom_components/phoenix_mcp/catalog_text.py", "custom_components/phoenix_mcp/tool_dispatch.py", "custom_components/phoenix_mcp/mcp_transport.py", "custom_components/phoenix_mcp/tool_defs.py"] }
---
Phoenix MCP lets AI agents work with your Home Assistant, safely. Each agent gets its own access key, and you decide what that key can see and do.

## Three ways to use it {#ways}

- **Agent Chat**: chat with an AI model right inside Home Assistant. You add an account with an AI provider, and Phoenix runs the agent for you.
- **An external app**: connect an MCP client, such as a desktop assistant or a coding tool, with an access key. The app's own model does the thinking.
- **Home Assistant's own assistants**: Phoenix can answer as a voice agent, run AI Task actions, or lend its tools to another assistant.

Whichever you use, every action goes through the same checks, and the audit log records it.

Related: [{{label:agentchat.title}}](guide:window.agentChat), [Connecting External Apps](guide:topic.connecting), [Voice Agent, AI Task or Assist Tool Provider](guide:topic.homeAssistantFeatures)

## What every action goes through {#checks}

1. **Capabilities**: what kinds of things the key may do at all, such as control devices or edit automations. Each is Deny, Allow or Confirm.
2. **Permissions**: which areas, devices and entities the key can see and change.
3. **MESA**: how careful to be with each device, whatever the key allows: let agents act, ask you first, or keep them off it.
4. **Approvals**: an action set to Confirm waits until you approve it.

Related: [What Agents Can Do](guide:topic.tools), [Security](guide:topic.security), [Running Phoenix](guide:topic.running), [Admin API](guide:topic.adminApi), [{{label:accessKeys.cardCapabilities}}](guide:window.capabilities), [How a permission is decided](guide:topic.permissionResolution), [How MESA Decides](guide:topic.mesaRules), [How an approval works](guide:topic.approvalLifecycle)

## Why not a long-lived access token {#token}

A Home Assistant long-lived access token acts as your user, across all of Home Assistant's interfaces. Anything holding it can do what you can.

An access key works only with Phoenix. It has its own permissions and optional expiry, and a rate limit on Phoenix's own connection. You can revoke it without touching your own login. Phoenix's connection doesn't accept Home Assistant tokens.

## Where to start {#start}

In the {{label:shell.tabAccessKeys}} workspace, select **{{label:accessKeys.setUpAgent}}**. The setup walks you through a key, its permissions, and either Agent Chat or an external app, then lets you test the connection.

Related: [How your agent connects](guide:wizard.choose), [Installing Phoenix MCP](guide:topic.install)
