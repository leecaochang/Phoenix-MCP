# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | [Français](README.fr.md) | [Italiano](README.it.md) | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | [Polski](README.pl.md) | **简体中文** | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Русский](README.ru.md)

Phoenix MCP 为您的 AI 智能体提供限定范围、最小权限的 Home Assistant 访问方式。每个客户端都拥有自己的访问密钥，只能访问您明确允许的实体，并各自拥有独立的功能、速率限制和可选的有效期。审计默认开启，并可按结果类型分别配置；任何访问密钥都可以立即吊销；逐实体的语义安全层（MESA）还能根据设备本身的性质，将其设为仅可在确认后操作或完全禁止操作，无论访问密钥被授予了什么权限。 MESA 默认使用建议模式：大多数策略限制仅发出警告，选择强制模式后才会阻止操作；只读属性和明确的隐私拒绝仍会阻止操作。

Phoenix MCP 完全运行在 Home Assistant 内部：无需另行部署服务，核心服务器不依赖任何云服务，除 Phoenix MCP 面板之外也无需任何设置（可选的智能体对话、语音和 AI 任务功能会将对话发送给您配置的模型提供商，除非您将它们指向本地的 Ollama）。它可以配合您已经在用的 MCP 客户端（Claude Code、Cursor、ChatGPT/Codex、Gemini 等），您也可以完全不安装客户端，直接在 Home Assistant 内部对话。无论采用哪种方式，引导式设置都能在几分钟内带您从新建访问密钥走到可用的智能体，背后是一个包含 169 个工具的目录，可用于读取、控制和编写您的配置。面板支持多种语言，默认跟随您的 Home Assistant 个人资料语言；如果该语言不可用，则回退到英文。 Home Companion 和可选的决策路由也可能向托管服务商发送请求及授权范围内的结果。外部 MCP 客户端遵循自身的服务商和数据共享设置。

<!-- readme-i18n:locale-only:start -->

Phoenix MCP 脱胎于一个被弃置的开源项目，它的名字也由此而来。我最初只是把界面翻译成中文，后来又修复了大量 bug。目前项目的运行状态非常良好。

需要说明的是，我在开发中借助了大语言模型（LLM）。我目前的职业是高级软件工程师，所有由 LLM 生成的代码都经过我本人的人工审阅和冒烟测试。

很抱歉，文档还没有从英文翻译过来。这项工作量很大，但如果有足够多的人需要，我会着手去做。

我打算长期维护这个项目，也有不少新功能的构想。欢迎提交新的想法或 issue，我会尽快回复。

<!-- readme-i18n:locale-only:end -->

## 语言

面板目前支持英语、西班牙语、法语、意大利语、德语、荷兰语、波兰语、俄语、简体中文、繁体中文、韩语和日语。如需手动更改面板语言，请打开 **Phoenix MCP > 设置 > 语言** 并选择一种语言。选择 **自动** 即可恢复跟随 Home Assistant 个人资料语言。

## 文档

完整文档包括工具参考、权限、功能、MESA 和管理 API，位于 **[leecaochang.github.io/Phoenix-MCP](https://leecaochang.github.io/Phoenix-MCP/)**。面板中的帮助链接也会指向这里。

第一次使用？请从**[快速入门](https://leecaochang.github.io/Phoenix-MCP/quickstart.html)**开始：它会创建您的第一个访问密钥，为其授予一台设备的访问权限，然后让您选择直接在 Home Assistant 内对话或连接外部智能体。无论选择哪种方式，几分钟即可完成。

## 要求

- Home Assistant 2026.7.0 或更高版本。
- 每个 Home Assistant 只能配置一个 Phoenix MCP 实例。Home Assistant 会根据自身的依赖约束安装 Python 依赖项 `Babel>=2.18.0`, `jsonschema>=4.26.0`；MESA 已随 Phoenix MCP 一起提供。

## 通过 HACS 安装

1. 在 HACS 中打开右上角菜单，然后选择 **自定义存储库**。
2. 输入 `https://github.com/leecaochang/Phoenix-MCP`，类别选择**集成**，然后点击**添加**。
3. 在 HACS 集成列表中找到 Phoenix MCP，安装后重新启动 Home Assistant。

希望手动安装？将 `custom_components/phoenix_mcp` 文件夹复制到 Home Assistant 配置目录下的 `custom_components/phoenix_mcp`，然后重新启动。

### 设置

前往**设置 > 设备与服务 > 添加集成**，搜索 **Phoenix MCP**。完成这个单步骤配置流程，然后打开侧边栏中的 **Phoenix MCP** 面板。之后请按照[快速入门](https://leecaochang.github.io/Phoenix-MCP/quickstart.html)继续操作。

## 问题与反馈

请在 [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues) 报告问题。
