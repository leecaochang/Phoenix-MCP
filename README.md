
# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


**English** | [Español](README.es.md) | [Français](README.fr.md) | [Italiano](README.it.md) | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | [Polski](README.pl.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Русский](README.ru.md) | [Svenska](README.sv.md) | [Dansk](README.da.md) | [Norsk Bokmål](README.nb.md) | [Suomi](README.fi.md)

Phoenix MCP gives your AI agents safe, limited access to Home Assistant through MCP (Model Context Protocol), an open standard for connecting AI apps to tools and data. Security and safety are crucial when using AI, and Phoenix MCP has undergone many months of development and security reviews. Each agent has its own access key, limited to only the entities and capabilities you allow. An additional safety layer (MESA) can make a selected entity confirm-only or off-limits, no matter what permissions an access key is granted.

Phoenix MCP runs entirely inside Home Assistant, with no separate service to deploy. Use the apps you already have, such as Claude Code, Cursor, ChatGPT, or Gemini, or chat from inside Home Assistant itself with your choice of AI provider, in the cloud or locally via Ollama.

A guided setup takes you from install to a working agent within minutes, backed by a catalog of 174 tools for reading, controlling, and authoring your configuration. Phoenix MCP uses a familiar and customizable windowed interface, making it easy to get started. Full documentation is built in and available at any time.

## Screenshots

![Access Keys window with the Permissions Tree](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/01-access-keys.png)

In **Access Keys**, each agent gets its own key. Choose a **Persona**, then use the **Permissions Tree** to decide what it can reach, down to a single entity.

![Approval request with a dashboard card preview](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/02-approvals.png)

In **Approvals**, you preview a dashboard card before it is added. Here an agent asked to add one, and nothing changes until you choose **Approve**.

![Agent Chat open over a dashboard](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/03-agent-chat.png)

**Agent Chat** is available everywhere in Home Assistant. Here it builds a card, then waits for your approval before it changes anything.

![My Workspace with Agent Chat docked](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/04-my-workspace.png)

**My Workspace** holds the windows you use most. Here **Agent Chat** sits beside the **Audit Log** and **Changes**.

## Languages

The panel currently supports English, Spanish, French, Italian, German, Dutch, Polish, Russian, Simplified Chinese, Traditional Chinese, Korean, Japanese, Swedish, Danish, Norwegian, and Finnish. To change the panel language manually, open **Phoenix MCP > Settings > Language** and choose a language. Choose **Auto** to follow your Home Assistant profile language again.

## Documentation

The manual is built into the panel: open the **Phoenix MCP** panel and choose **Guide** from the **Help** menu. It explains every window and setting, what agents can do, permissions, capabilities, MESA, and security. It follows what you select, and you can search it.

First time here? After the setup below, open the **Phoenix MCP** panel and choose **Set Up an Agent**. A wizard walks you through creating an access key, choosing what it can do, and connecting Agent Chat or your own app.

## Requirements

- Home Assistant 2026.7.0 or later.
- One Phoenix MCP instance per Home Assistant. Home Assistant installs the dependencies `Babel>=2.18.0` and `jsonschema>=4.26.0` for you.

## Install from the Marketplace

On Home Assistant 2026.11 or later:

1. Go to **Settings > Marketplace** and choose **Add from link**.
2. Enter `https://github.com/leecaochang/Phoenix-MCP` and choose **Add**. The Marketplace asks you to connect GitHub the first time.
3. Open Phoenix MCP and choose **Install**. Restart Home Assistant only if it asks you to.

If you upgrade from HACS, Phoenix MCP moves to the Marketplace by itself. Connect GitHub there to keep getting updates.

## Install via HACS

On Home Assistant 2026.10 or earlier:

1. In HACS, open the top-right menu and choose **Custom repositories**.
2. Enter `https://github.com/leecaochang/Phoenix-MCP` and select **Integration** as the category, then click **Add**.
3. Find Phoenix MCP in the HACS integration list, install it, and restart Home Assistant.

Prefer to install by hand? Copy the `custom_components/phoenix_mcp` folder into your Home Assistant config directory under `custom_components/phoenix_mcp`, then restart.

## Set up

Go to **Settings > Devices & services > Add integration** and search for **Phoenix MCP**. Complete the one-step setup, then open the **Phoenix MCP** panel in your sidebar and choose **Set Up an Agent**.

## Issues and feedback

Report issues at [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues).
