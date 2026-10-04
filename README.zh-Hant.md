# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | [Français](README.fr.md) | [Italiano](README.it.md) | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | [Polski](README.pl.md) | [简体中文](README.zh-CN.md) | **繁體中文** | [日本語](README.ja.md) | [한국어](README.ko.md) | [Русский](README.ru.md)

Phoenix MCP 為您的 AI 智慧體提供限定範圍、最小權限的 Home Assistant 存取方式。每個用戶端都有自己的存取金鑰，只能存取您明確允許的實體，並各自擁有獨立的能力、速率限制和可選的有效期限。稽核預設開啟，並可按結果類型分別設定；任何存取金鑰都可以立即撤銷；逐實體的語意安全層（MESA）還能根據裝置本身的性質，將其設為僅可在確認後操作或完全禁止操作，無論存取金鑰被授予了什麼權限。 MESA 預設使用建議模式：大多數策略限制僅發出警告，選擇強制模式後才會阻止操作；唯讀屬性和明確的隱私拒絕仍會阻止操作。

Phoenix MCP 完全執行於 Home Assistant 內部：無需額外程序，核心伺服器不依賴任何雲端服務，除 Phoenix MCP 面板之外也無需任何設定（可選的智慧體對話、語音和 AI 任務功能會將對話傳送給您設定的模型提供者，除非您將它們指向本機的 Ollama）。它可以配合您已經在用的 MCP 用戶端（Claude Code、Cursor、ChatGPT/Codex、Gemini 等），您也可以完全不安裝用戶端，直接在 Home Assistant 內部對話。無論採用哪種方式，引導式設定都能在幾分鐘內帶您從新建存取金鑰走到可用的智慧體，背後是一個包含 169 個工具的目錄，可用於讀取、控制和編寫您的設定。面板支援多種語言，預設跟隨您的 Home Assistant 個人資料語言；如果該語言不可用，則回退至英文。 Home Companion 和選用的決策路由也可能向託管服務商傳送請求及授權範圍內的結果。外部 MCP 用戶端遵循自身的服務商和資料共用設定。

## 語言

面板目前支援英文、西班牙文、法文、義大利文、德文、荷蘭文、波蘭文、俄文、簡體中文、繁體中文、韓文和日文。如要手動變更面板語言，請開啟 **Phoenix MCP > 設定 > 語言** 並選擇語言。選擇 **自動** 即可恢復跟隨 Home Assistant 個人資料語言。

## 文件

完整文件包括工具參考、權限、能力、MESA 和管理 API，位於 **[leecaochang.github.io/Phoenix-MCP](https://leecaochang.github.io/Phoenix-MCP/)**。面板中的說明連結也會指向這裡。

第一次使用？請從**[快速入門](https://leecaochang.github.io/Phoenix-MCP/quickstart.html)**開始：它會建立您的第一個存取金鑰，為其授予一台裝置的存取權限，然後讓您選擇直接在 Home Assistant 內對話或連接外部智慧體。無論選擇哪種方式，幾分鐘即可完成。

## 需求

- Home Assistant 2026.7.0 或更高版本。
- 每個 Home Assistant 只能設定一個 Phoenix MCP 實例。Home Assistant 會根據自身的相依性限制安裝 Python 相依套件 `voluptuous-serialize>=2.7.0`, `Babel>=2.18.0`, `jsonschema>=4.26.0`；MESA 已隨 Phoenix MCP 一起提供。

## 透過 HACS 安裝

1. 在 HACS 中開啟右上角選單，然後選擇 **自訂儲存庫**。
2. 輸入 `https://github.com/leecaochang/Phoenix-MCP`，類別選擇**整合**，然後點選**新增**。
3. 在 HACS 整合清單中找到 Phoenix MCP，安裝後重新啟動 Home Assistant。

希望手動安裝？將 `custom_components/phoenix_mcp` 資料夾複製到 Home Assistant 設定目錄下的 `custom_components/phoenix_mcp`，然後重新啟動。

### 設定

前往**設定 > 裝置與服務 > 新增整合**，搜尋 **Phoenix MCP**。完成這個單步驟設定流程，然後開啟側邊欄中的 **Phoenix MCP** 面板。之後請按照[快速入門](https://leecaochang.github.io/Phoenix-MCP/quickstart.html)繼續操作。

## 問題與回饋

請在 [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues) 回報問題。
