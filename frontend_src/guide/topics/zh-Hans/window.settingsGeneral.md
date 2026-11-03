---
{ "id": "window.settingsGeneral", "title": "{{label:workspace.general}}", "parent": "workspace.settings", "order": 6, "sectionKeywords": { "language": ["翻译", "区域设置"], "theme": ["深色模式", "浅色模式"], "color-theme": ["颜色", "调色板", "外观", "Spartan", "Wayfinding", "Framed", "Mist", "Office", "Aubergine"], "presets": ["预设"], "inject": ["策略按钮", "MESA"], "about": ["版本"] } }
---
选择面板的语言和主题，开启或关闭可选功能，并查看你的版本。

## {{label:settings.languageLabel}} {#language}

面板的语言。**{{label:settings.languageAuto}}** 使用你的 Home Assistant 个人资料语言；如果 Phoenix 不支持该语言，则使用英文。每种语言都以自己的名称列出。

此选择仅对当前浏览器有效，并会立即更改面板和 Agent Chat。

## {{label:settings.themeLabel}} {#theme}

{{label:settings.themeLight}}、{{label:settings.themeAuto}}（默认）或 {{label:settings.themeDark}}。{{label:settings.themeAuto}} 跟随 Home Assistant 的深色模式。此选择仅对当前浏览器有效。

## {{label:settings.colorThemeLabel}} {#color-theme}

更改整个面板、指南和 Agent Chat 的颜色。每种配色主题都有浅色和深色版本，由 {{label:settings.themeLabel}} 决定你看到哪一个。此选择仅对当前浏览器有效，并会立即更改所有内容。

- **{{label:settings.colorTheme.spartan}}**（默认）：黑白配色，只在有意义的地方使用颜色。
- **{{label:settings.colorTheme.wayfinding}}**：类似 {{label:settings.colorTheme.spartan}}，但每个工作区在其工具栏按钮和窗口图标上有自己的颜色。
- **{{label:settings.colorTheme.framed}}**：窗口有彩色边框和标题栏，你正在使用的窗口边框更醒目。
- **{{label:settings.colorTheme.mist}}**：整体柔和的蓝色，配蓝色按钮和选中效果。
- **{{label:settings.colorTheme.office}}**：蓝色工具栏带和白色窗口，类似桌面办公套件。
- **{{label:settings.colorTheme.aubergine}}**：深梅色工具栏带配梅色按钮，类似流行的团队聊天应用。

状态颜色在每个主题中都保持含义：绿色表示允许，红色表示拒绝访问，琥珀色表示等待你。在 {{label:settings.colorTheme.mist}} 和 {{label:settings.colorTheme.office}} 中，读取权限显示为紫色，因为蓝色已经是它们的按钮颜色。

除 {{label:settings.colorTheme.spartan}} 外的每个主题还会为三个角色着色：{{label:personas.home_companion.label}}为金色，{{label:personas.new_user.label}}为绿色，{{label:personas.custom.label}}为蓝色。

## {{label:settings.presetsLabel}} {#presets}

预设会保存访问密钥的整套设置，让你可以在不同设置之间切换密钥。开启预设会给每个密钥一个起始预设，并在访问密钥中显示 **{{label:accessKeys.cardPresets}}** 窗口。默认关闭。

每个密钥最多可以保存 8 个预设。关闭预设会隐藏它们，但不会删除它们。

相关：[访问密钥](guide:workspace.accessKeys)

## {{label:settings.injectLabel}} {#inject}

向 Home Assistant 自己的页面添加 MESA 策略按钮。包括实体、自动化、脚本、场景、辅助元素和人员列表，以及设备、区域和集成页面。只有管理员能看到它们。默认关闭。

更改会在页面下次完整加载时生效。如果按钮在某个页面上失败，它们会关闭直到页面重新加载，但设置仍保持开启。

相关：[MESA](guide:workspace.mesa)

## {{label:settings.about}} {#about}

显示 Phoenix 版本、面板构建版本、Phoenix 支持的最旧 Home Assistant 版本，以及项目仓库的链接。
