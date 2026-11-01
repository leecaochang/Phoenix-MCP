---
{ "id": "window.settingsGeneral", "title": "{{label:workspace.general}}", "parent": "workspace.settings", "order": 6, "sectionKeywords": { "language": ["translation", "locale"], "theme": ["dark mode", "light mode"], "presets": ["presets"], "inject": ["profile buttons", "MESA"], "about": ["version"] }, "covers": ["frontend_src/views/SettingsWindows.tsx", "frontend_src/i18n/index.ts", "frontend_src/inject/index.ts"] }
---
Choose the panel's language and theme, turn optional features on or off, and see which version you have.

## {{label:settings.languageLabel}} {#language}

The panel's language. **{{label:settings.languageAuto}}** uses your Home Assistant profile's language, or English if Phoenix doesn't have it. Each language is listed in its own name.

This choice applies to this browser only, and changes the panel and Agent Chat straight away.

## {{label:settings.themeLabel}} {#theme}

{{label:settings.themeLight}}, {{label:settings.themeAuto}} (default) or {{label:settings.themeDark}}. {{label:settings.themeAuto}} follows Home Assistant's dark mode. This choice applies to this browser only.

## {{label:settings.presetsLabel}} {#presets}

A preset saves an access key's whole setup, so you can switch the key between setups. Turning presets on gives every key a starting preset, and shows the **{{label:accessKeys.cardPresets}}** window in Access Keys. Off by default.

Each key can hold up to 8 presets. Turning presets off hides them, but doesn't delete them.

Related: [Access Keys](guide:workspace.accessKeys)

## {{label:settings.injectLabel}} {#inject}

Adds MESA profile buttons to Home Assistant's own pages: the entity, automation, script, scene, helper and people lists, and the device, area and integration pages. Only administrators see them. Off by default.

A change takes effect the next time the page fully loads. If the buttons fail on a page, they turn off until the page reloads, but the setting stays on.

Related: [MESA](guide:workspace.mesa)

## {{label:settings.about}} {#about}

Shows the Phoenix version, the panel's build, the oldest Home Assistant version Phoenix supports, and a link to the project's repository.
