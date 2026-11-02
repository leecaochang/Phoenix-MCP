---
{ "id": "window.settingsGeneral", "title": "{{label:workspace.general}}", "parent": "workspace.settings", "order": 6, "sectionKeywords": { "language": ["translation", "locale"], "theme": ["dark mode", "light mode"], "color-theme": ["colors", "palette", "appearance", "Spartan", "Wayfinding", "Framed", "Mist", "Office", "Aubergine"], "presets": ["presets"], "inject": ["profile buttons", "MESA"], "about": ["version"] }, "covers": ["frontend_src/views/SettingsWindows.tsx", "frontend_src/i18n/index.ts", "frontend_src/inject/index.ts", "frontend_src/ui/theme.ts", "frontend_src/themes.css"] }
---
Choose the panel's language and theme, turn optional features on or off, and see which version you have.

## {{label:settings.languageLabel}} {#language}

The panel's language. **{{label:settings.languageAuto}}** uses your Home Assistant profile's language, or English if Phoenix doesn't have it. Each language is listed in its own name.

This choice applies to this browser only, and changes the panel and Agent Chat straight away.

## {{label:settings.themeLabel}} {#theme}

{{label:settings.themeLight}}, {{label:settings.themeAuto}} (default) or {{label:settings.themeDark}}. {{label:settings.themeAuto}} follows Home Assistant's dark mode. This choice applies to this browser only.

## {{label:settings.colorThemeLabel}} {#color-theme}

Changes the colors of the whole panel, the Guide and Agent Chat. Each color theme has a light and a dark version, and {{label:settings.themeLabel}} picks which one you see. This choice applies to this browser only, and changes everything straight away.

- **{{label:settings.colorTheme.spartan}}** (default): black and white, with color only where it means something.
- **{{label:settings.colorTheme.wayfinding}}**: like {{label:settings.colorTheme.spartan}}, but each workspace has its own color on its toolbar button and on the icons in its windows.
- **{{label:settings.colorTheme.framed}}**: windows get a colored frame and title bar, and the window you're working in has the stronger frame.
- **{{label:settings.colorTheme.mist}}**: soft blue throughout, with blue buttons and selection.
- **{{label:settings.colorTheme.office}}**: a blue toolbar band and white windows, like a desktop office suite.
- **{{label:settings.colorTheme.aubergine}}**: a deep plum toolbar band with plum buttons, like a popular team chat app.

Status colors keep their meaning in every theme: green for allowed, red for denied, amber for waiting on you. In {{label:settings.colorTheme.mist}} and {{label:settings.colorTheme.office}}, Read access shows in violet, because blue is already their button color.

Every theme except {{label:settings.colorTheme.spartan}} also colors three personas: {{label:personas.home_companion.label}} gold, {{label:personas.new_user.label}} green and {{label:personas.custom.label}} blue.

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
