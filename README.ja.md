# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | [Français](README.fr.md) | [Italiano](README.it.md) | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | [Polski](README.pl.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-Hant.md) | **日本語** | [한국어](README.ko.md) | [Русский](README.ru.md) | [Svenska](README.sv.md) | [Dansk](README.da.md) | [Norsk Bokmål](README.nb.md) | [Suomi](README.fi.md)

Phoenix MCP は、MCP（Model Context Protocol、AI アプリをツールやデータに接続するためのオープン標準）を通じて、AI エージェントに安全で限定された Home Assistant へのアクセスを提供します。AI を使ううえで安全性は非常に重要であり、Phoenix MCP は長期間の開発とセキュリティレビューを経ています。エージェントごとに専用のアクセスキーがあり、許可したエンティティと機能だけに制限されます。追加の安全レイヤー（[MESA](https://github.com/leecaochang/mesa-core)）により、アクセスキーに付与された権限にかかわらず、選択したエンティティを確認必須または操作禁止にできます。

Phoenix MCP は Home Assistant の中だけで動作し、別途サービスを導入する必要はありません。Claude Code、Cursor、ChatGPT、Gemini など、お使いのアプリを利用できるほか、選択した AI プロバイダーを使って Home Assistant の中で直接チャットすることもできます。プロバイダーはクラウドでも、Ollama によるローカルでも構いません。

ガイド付きセットアップにより、インストールから動作するエージェントまで数分で進めることができます。さらに、設定の読み取り、操作、作成に使える 174 個のツールのカタログが用意されています。Phoenix MCP は、使い慣れたカスタマイズ可能なウィンドウ型インターフェイスを採用しているので、すぐに始められます。完全なドキュメントは組み込まれており、いつでも参照できます。

## スクリーンショット

![アクセスキーウィンドウと権限ツリー](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/01-access-keys.png)

**アクセスキー**では、エージェントごとに専用のキーを用意します。**ペルソナ**を選び、**権限ツリー**でアクセスできる範囲を単一のエンティティまで指定します。

![ダッシュボードカードのプレビューが付いた承認リクエスト](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/02-approvals.png)

**承認**では、ダッシュボードカードが追加される前にプレビューを確認します。この例ではエージェントがカードの追加を依頼しており、**承認**を選ぶまで何も変更されません。

![ダッシュボードの上に開いた Agent Chat](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/03-agent-chat.png)

**Agent Chat** は Home Assistant のどこでも利用できます。この例ではカードを作成し、変更を加える前に承認を待っています。

![Agent Chat をドッキングしたマイワークスペース](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/04-my-workspace.png)

**マイワークスペース**には、よく使うウィンドウを集められます。この例では **Agent Chat** が**監査ログ**と**変更履歴**の横にあります。

## 言語

パネルは現在、英語、スペイン語、フランス語、イタリア語、ドイツ語、オランダ語、ポーランド語、ロシア語、簡体字中国語、繁体字中国語、韓国語、日本語、スウェーデン語、デンマーク語、ノルウェー語、フィンランド語に対応しています。パネルの言語を手動で変更するには、**Phoenix MCP > 設定 > 言語**を開いて言語を選択します。**自動**を選ぶと、Home Assistant プロファイルの言語に戻ります。

## ドキュメント

マニュアルはパネルに組み込まれています。**Phoenix MCP** パネルを開き、**ヘルプ**メニューから**ガイド**を選択してください。すべてのウィンドウと設定、エージェントにできること、権限、機能、MESA、セキュリティについて説明しています。選択した内容に合わせて表示が変わり、検索もできます。

初めてお使いですか？下記のセットアップの後、**Phoenix MCP** パネルを開き、**エージェントを設定**を選択してください。ウィザードの案内に従って、アクセスキーの作成、できることの選択、Agent Chat または独自のアプリの接続を行います。

## 動作要件

- Home Assistant 2026.7.0 以降。
- Home Assistant ごとに Phoenix MCP インスタンスを一つだけ設定できます。Home Assistant が依存関係 `Babel>=2.18.0` と `jsonschema>=4.26.0` を自動でインストールします。

## マーケットプレイスからインストール

Home Assistant 2026.11 以降の場合:

1. **設定 > マーケットプレイス** を開き、**リンクから追加** を選択します。
2. `https://github.com/leecaochang/Phoenix-MCP` を入力し、**追加** を選択します。初回は、マーケットプレイスから GitHub への接続を求められます。
3. Phoenix MCP を開き、**インストール** を選択します。Home Assistant の再起動は、求められた場合にのみ行ってください。

HACS からアップグレードすると、Phoenix MCP は自動的にマーケットプレイスへ移行します。更新を受け取り続けるには、そこで GitHub に接続してください。

## HACS からインストール

Home Assistant 2026.10 以前の場合:

1. HACS の右上のメニューを開き、**カスタムリポジトリ**を選択します。
2. `https://github.com/leecaochang/Phoenix-MCP` を入力し、カテゴリとして**インテグレーション**を選択して、**追加**をクリックします。
3. HACS のインテグレーション一覧で Phoenix MCP を見つけてインストールし、Home Assistant を再起動します。

手動でインストールする場合は、`custom_components/phoenix_mcp` フォルダーを Home Assistant の設定ディレクトリ内の `custom_components/phoenix_mcp` にコピーしてから、再起動してください。

## セットアップ

**設定 > デバイスとサービス > 統合を追加**を開き、**Phoenix MCP** を検索します。一段階のセットアップを完了したら、サイドバーから **Phoenix MCP** パネルを開き、**エージェントを設定**を選択してください。

## 問題の報告とフィードバック

問題は [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues) で報告してください。
