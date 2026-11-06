# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | [Français](README.fr.md) | [Italiano](README.it.md) | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | [Polski](README.pl.md) | **简体中文** | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Русский](README.ru.md) | [Svenska](README.sv.md) | [Dansk](README.da.md) | [Norsk Bokmål](README.nb.md) | [Suomi](README.fi.md)

Phoenix MCP 通过 MCP（Model Context Protocol，一种连接 AI 应用与工具和数据的开放标准）为您的 AI 智能体提供安全、受限的 Home Assistant 访问。使用 AI 时，安全至关重要，Phoenix MCP 经过了许多个月的开发和安全审查。每个智能体都有自己的访问密钥，只能使用您允许的实体和功能。额外的安全层（MESA）可以让选定的实体必须经您确认才能操作，或完全禁止操作，无论访问密钥被授予了什么权限。

Phoenix MCP 完全在 Home Assistant 内运行，无需部署单独的服务。您可以使用已有的应用，例如 Claude Code、Cursor、ChatGPT 或 Gemini，也可以直接在 Home Assistant 内，通过您选择的 AI 提供商聊天，提供商可以在云端，也可以通过 Ollama 在本地运行。

引导式设置让您在几分钟内从安装到获得可用的智能体，并提供包含 174 个工具的目录，用于读取、控制和编写您的配置。Phoenix MCP 使用熟悉且可自定义的窗口式界面，上手非常容易。完整文档已内置，随时可用。

<!-- readme-i18n:locale-only:start -->

Phoenix MCP 脱胎于一个被弃置的开源项目，它的名字也由此而来。我最初只是把界面翻译成中文，后来又修复了大量 bug。目前项目的运行状态非常良好。

需要说明的是，我在开发中借助了大语言模型（LLM）。我目前的职业是高级软件工程师，所有由 LLM 生成的代码都经过我本人的人工审阅和冒烟测试。

很抱歉，文档还没有从英文翻译过来。这项工作量很大，但如果有足够多的人需要，我会着手去做。

我打算长期维护这个项目，也有不少新功能的构想。欢迎提交新的想法或 issue，我会尽快回复。

<!-- readme-i18n:locale-only:end -->

## 屏幕截图

![访问密钥窗口和权限树](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/01-access-keys.png)

在**访问密钥**中，每个智能体都有自己的密钥。选择一个**角色**，然后在**权限树**中决定它能访问什么，可以细化到单个实体。

![审批请求和仪表盘卡片预览](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/02-approvals.png)

在**审批**中，您会在仪表盘卡片添加之前看到它的预览。这里智能体请求添加一张卡片，在您选择**批准**之前不会发生任何变更。

![在仪表盘上方打开的 Agent Chat](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/03-agent-chat.png)

**Agent Chat** 在 Home Assistant 中随处可用。这里它创建了一张卡片，并在做出任何更改之前等待您的批准。

![停靠了 Agent Chat 的我的工作区](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/04-my-workspace.png)

**我的工作区**汇集您最常用的窗口。这里 **Agent Chat** 位于**审计日志**和**变更**旁边。

## 语言

面板目前支持英语、西班牙语、法语、意大利语、德语、荷兰语、波兰语、俄语、简体中文、繁体中文、韩语、日语、瑞典语、丹麦语、挪威语和芬兰语。如需手动更改面板语言，请打开 **Phoenix MCP > 设置 > 语言** 并选择一种语言。选择 **自动** 即可恢复跟随 Home Assistant 个人资料语言。

## 文档

使用手册内置于面板中：打开 **Phoenix MCP** 面板，从**帮助**菜单中选择**指南**。它介绍每个窗口和设置、智能体可以做什么、权限、功能、MESA 和安全。它会跟随您的选择，并且支持搜索。

第一次使用？完成下方的设置后，打开 **Phoenix MCP** 面板并选择**设置智能体**。向导会引导您创建访问密钥、选择它可以做什么，并连接 Agent Chat 或您自己的应用。

## 要求

- Home Assistant 2026.7.0 或更高版本。
- 每个 Home Assistant 只能配置一个 Phoenix MCP 实例。Home Assistant 会为您安装依赖项 `Babel>=2.18.0` 和 `jsonschema>=4.26.0`。

## 通过市场安装

在 Home Assistant 2026.11 或更高版本上：

1. 前往 **设置 > 市场**，然后选择 **从链接添加**。
2. 输入 `https://github.com/leecaochang/Phoenix-MCP`，然后选择 **添加**。首次添加时，市场会要求您连接 GitHub。
3. 打开 Phoenix MCP，选择 **安装**。仅在 Home Assistant 要求时才需要重新启动。

如果您从 HACS 升级，Phoenix MCP 会自动转到市场。请在那里连接 GitHub，以便继续获得更新。

## 通过 HACS 安装

在 Home Assistant 2026.10 或更早版本上：

1. 在 HACS 中打开右上角菜单，然后选择 **自定义存储库**。
2. 输入 `https://github.com/leecaochang/Phoenix-MCP`，类别选择**集成**，然后点击**添加**。
3. 在 HACS 集成列表中找到 Phoenix MCP，安装后重新启动 Home Assistant。

希望手动安装？将 `custom_components/phoenix_mcp` 文件夹复制到 Home Assistant 配置目录下的 `custom_components/phoenix_mcp`，然后重新启动。

## 设置

前往**设置 > 设备与服务 > 添加集成**，搜索 **Phoenix MCP**。完成这个单步骤设置，然后打开侧边栏中的 **Phoenix MCP** 面板并选择**设置智能体**。

## 问题与反馈

请在 [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues) 报告问题。
