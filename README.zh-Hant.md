# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | [Français](README.fr.md) | [Italiano](README.it.md) | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | [Polski](README.pl.md) | [简体中文](README.zh-CN.md) | **繁體中文** | [日本語](README.ja.md) | [한국어](README.ko.md) | [Русский](README.ru.md) | [Svenska](README.sv.md) | [Dansk](README.da.md) | [Norsk Bokmål](README.nb.md) | [Suomi](README.fi.md)

Phoenix MCP 透過 MCP（Model Context Protocol，一種連接 AI 應用程式與工具和資料的開放標準）為您的 AI 智慧體提供安全、受限的 Home Assistant 存取。使用 AI 時，安全至關重要，Phoenix MCP 經過了許多個月的開發和安全審查。每個智慧體都有自己的存取金鑰，只能使用您允許的實體和能力。額外的安全層（[MESA](https://github.com/leecaochang/mesa-core)）可以讓選定的實體必須經您確認才能操作，或完全禁止操作，無論存取金鑰被授予了什麼權限。

Phoenix MCP 完全在 Home Assistant 內執行，無需部署單獨的服務。您可以使用現有的應用程式，例如 Claude Code、Cursor、ChatGPT 或 Gemini，也可以直接在 Home Assistant 內，透過您選擇的 AI 供應商聊天，供應商可以在雲端，也可以透過 Ollama 在本機執行。

引導式設定讓您在幾分鐘內從安裝到獲得可用的智慧體，並提供包含 174 個工具的目錄，用於讀取、控制和編寫您的設定。Phoenix MCP 使用熟悉且可自訂的視窗式介面，上手非常容易。完整文件已內建，隨時可用。

## 螢幕截圖

![存取金鑰視窗和權限樹](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/01-access-keys.png)

在**存取金鑰**中，每個智慧體都有自己的金鑰。選擇一個**角色**，然後在**權限樹**中決定它能存取什麼，可以細化到單一實體。

![核准請求和儀表板卡片預覽](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/02-approvals.png)

在**核准**中，您會在儀表板卡片新增之前看到它的預覽。這裡智慧體請求新增一張卡片，在您選擇**核准**之前不會有任何變更。

![在儀表板上方開啟的 Agent Chat](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/03-agent-chat.png)

**Agent Chat** 在 Home Assistant 中隨處可用。這裡它建立了一張卡片，並在做出任何變更之前等待您的核准。

![停靠了 Agent Chat 的我的工作區](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/04-my-workspace.png)

**我的工作區**匯集您最常用的視窗。這裡 **Agent Chat** 位於**稽核日誌**和**變更**旁邊。

## 語言

面板目前支援英文、西班牙文、法文、義大利文、德文、荷蘭文、波蘭文、俄文、簡體中文、繁體中文、韓文、日文、瑞典文、丹麥文、挪威文和芬蘭文。如要手動變更面板語言，請開啟 **Phoenix MCP > 設定 > 語言** 並選擇語言。選擇 **自動** 即可恢復跟隨 Home Assistant 個人資料語言。

## 文件

使用手冊內建於面板中：開啟 **Phoenix MCP** 面板，從**說明**選單中選擇**指南**。它說明每個視窗和設定、智慧體可以做什麼、權限、能力、MESA 和安全性。它會跟隨您的選擇，並且支援搜尋。

第一次使用？完成下方的設定後，開啟 **Phoenix MCP** 面板並選擇**設定智慧體**。精靈會引導您建立存取金鑰、選擇它可以做什麼，並連接 Agent Chat 或您自己的應用程式。

## 需求

- Home Assistant 2026.7.0 或更高版本。
- 每個 Home Assistant 只能設定一個 Phoenix MCP 實例。Home Assistant 會為您安裝相依套件 `Babel>=2.18.0` 和 `jsonschema>=4.26.0`。

## 透過 Marketplace 安裝

在 Home Assistant 2026.11 或更新版本上：

1. 前往 **設定 > Marketplace**，然後選擇 **從連結新增**。
2. 輸入 `https://github.com/leecaochang/Phoenix-MCP`，然後選擇 **增加**。第一次新增時，Marketplace 會要求您連接 GitHub。
3. 開啟 Phoenix MCP，選擇 **安裝**。僅在 Home Assistant 要求時才需要重新啟動。

如果您從 HACS 升級，Phoenix MCP 會自動轉到 Marketplace。請在那裡連接 GitHub，以便繼續取得更新。

## 透過 HACS 安裝

在 Home Assistant 2026.10 或更舊版本上：

1. 在 HACS 中開啟右上角選單，然後選擇 **自訂儲存庫**。
2. 輸入 `https://github.com/leecaochang/Phoenix-MCP`，類別選擇**整合**，然後點選**新增**。
3. 在 HACS 整合清單中找到 Phoenix MCP，安裝後重新啟動 Home Assistant。

希望手動安裝？將 `custom_components/phoenix_mcp` 資料夾複製到 Home Assistant 設定目錄下的 `custom_components/phoenix_mcp`，然後重新啟動。

## 設定

前往**設定 > 裝置與服務 > 新增整合**，搜尋 **Phoenix MCP**。完成這個單步驟設定，然後開啟側邊欄中的 **Phoenix MCP** 面板並選擇**設定智慧體**。

## 問題與回饋

請在 [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues) 回報問題。
