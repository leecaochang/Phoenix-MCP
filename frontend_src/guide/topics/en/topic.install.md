---
{ "id": "topic.install", "title": "Installing Phoenix MCP", "parent": "topic.about", "order": 10, "keywords": ["install", "setup", "HACS", "requirements"], "sectionKeywords": { "requirements": ["version", "minimum"], "hacs": ["custom repository", "download"], "manual": ["custom_components", "copy"], "setup": ["add integration", "devices and services"], "panel": ["sidebar", "administrator"], "language": ["translation", "16 languages"], "removing": ["uninstall", "delete", "remove integration"] }, "covers": ["custom_components/phoenix_mcp/config_flow.py", "custom_components/phoenix_mcp/panel.py", "custom_components/phoenix_mcp/const.py", "custom_components/phoenix_mcp/__init__.py", "frontend_src/i18n/index.ts"] }
---
Phoenix MCP is a Home Assistant integration. Install it, add it once, and its panel appears in the sidebar.

## Requirements {#requirements}

Home Assistant 2026.7.0 or newer. Phoenix needs nothing else installed: its safety layer, MESA, comes with it.

## Installing with HACS {#hacs}

In HACS, find Phoenix MCP, or add its repository as a custom repository of type Integration. Download it, then restart Home Assistant. HACS checks the Home Assistant version for you.

## Installing by hand {#manual}

Copy the `phoenix_mcp` folder into your Home Assistant's `custom_components` folder, then restart Home Assistant. On an older Home Assistant, Phoenix doesn't start, and the Home Assistant log says which version it needs.

## Adding the integration {#setup}

In Home Assistant, go to Settings, Devices & services, then Add integration, and choose Phoenix MCP. There's nothing to fill in. You can add it only once.

## The panel {#panel}

**Phoenix MCP** appears in the sidebar for administrators only. Everything else is set up from there, starting with an access key.

Each access key also gets a device in Home Assistant, with sensors for its status, requests, refusals, rate limit hits, last use and expiry. Agents can't see these sensors.

## Language {#language}

The panel speaks 16 languages. By default it follows your Home Assistant profile; choose another in [{{label:settings.languageLabel}}](guide:window.settingsGeneral#language). The choice applies to this browser only. The Guide shows each page in your language when it's translated, and in English otherwise.

## Removing Phoenix MCP {#removing}

Deleting the integration revokes every access key, cancels requests waiting for approval, and erases every AI provider account and its API key. It also unlinks Phoenix from Assist, the voice agent, AI Task and the decision provider.

Everything else stays in Home Assistant's storage: settings such as the kill switch and MESA mode, the audit log, Conversation Log, MESA profiles, change history and recovery copies. If you add Phoenix again, it picks them up, and the revoked keys are listed in Archived Access Keys.

To remove Phoenix's data too, use **{{label:settings.wipeCoreLabel}}** in [{{label:settings.wipeTitle}}](guide:window.settingsSafety#wipe-core) before deleting the integration.
