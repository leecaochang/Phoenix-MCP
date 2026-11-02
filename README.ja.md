# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | [Français](README.fr.md) | [Italiano](README.it.md) | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | [Polski](README.pl.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-Hant.md) | **日本語** | [한국어](README.ko.md) | [Русский](README.ru.md) | [Svenska](README.sv.md) | [Dansk](README.da.md) | [Norsk Bokmål](README.nb.md) | [Suomi](README.fi.md)

Phoenix MCP は、AI エージェントに対して、範囲を限定した最小権限の Home Assistant アクセスを提供します。クライアントごとに専用のアクセスキーが発行され、明示的に許可したエンティティだけにアクセスできます。各アクセスキーには、個別の機能、レート制限、任意の有効期限を設定できます。監査はデフォルトで有効になっており、結果ごとに設定できます。どのアクセスキーも即座に取り消せます。また、エンティティ単位のセマンティック安全レイヤー（MESA）により、アクセスキーに付与された権限にかかわらず、デバイスの性質に応じて操作を確認必須または禁止にできます。 MESA の既定モードはアドバイザリです。強制を選択するまで、ほとんどのポリシー制限は警告のみになります。読み取り専用の性質と明示的なプライバシー拒否は引き続き操作をブロックします。

Phoenix MCP は Home Assistant 内で完結して動作します。別途デプロイするサービスは不要で、コアサーバーはクラウドに依存せず、Phoenix MCP パネル以外の設定も必要ありません（オプションの Agent Chat、音声、AI タスク機能では、ローカルの Ollama を指定しない限り、設定したモデルプロバイダーへ会話が送信されます）。Claude Code、Cursor、ChatGPT/Codex、Gemini など、すでにお使いの MCP クライアントと連携できます。クライアントをインストールせず、Home Assistant 内から直接チャットすることもできます。どちらの場合も、ガイド付きセットアップに従えば、新しいアクセスキーの作成からエージェントの利用開始まで数分で完了します。設定の読み取り、制御、作成・編集に対応する 174 個のツールが用意されています。パネルは複数の言語に対応しており、デフォルトでは Home Assistant プロファイルの言語に従い、その言語が利用できない場合は英語にフォールバックします。 Home Companion と任意の判断ルーティングも、リクエストと許可範囲内の結果をホスト型プロバイダーへ送信する場合があります。外部 MCP クライアントは、それぞれのプロバイダー設定とデータ共有設定に従います。

## 言語

パネルは現在、英語、スペイン語、フランス語、イタリア語、ドイツ語、オランダ語、ポーランド語、ロシア語、簡体字中国語、繁体字中国語、韓国語、日本語、スウェーデン語、デンマーク語、ノルウェー語、フィンランド語に対応しています。パネルの言語を手動で変更するには、**Phoenix MCP > 設定 > 言語**を開いて言語を選択します。**自動**を選ぶと、Home Assistant プロファイルの言語に戻ります。

## ドキュメント

マニュアルはパネルに組み込まれています。**Phoenix MCP** パネルを開き、**ドキュメント**メニューから**ガイド**を選択してください。すべてのウィンドウと設定、エージェントにできること、権限、機能、MESA、セキュリティ、管理 API について説明しており、検索もできます。

初めてお使いですか？**Phoenix MCP** パネルを開き、アクセスキーで**エージェントを設定**を選択してください。最初のアクセスキーを作成し、そのキーにできることを決めた後、Home Assistant 内でチャットするか、外部アプリを接続できます。最後に、動作をその場で確認できるテストを行います。

## 動作要件

- Home Assistant 2026.7.0 以降。
- Home Assistant ごとに Phoenix MCP インスタンスを一つだけ設定できます。Home Assistant は自身の依存関係制約に従って Python 依存関係 `Babel>=2.18.0`, `jsonschema>=4.26.0` をインストールします。MESA は Phoenix MCP に同梱されています。

## HACS からインストール

1. HACS の右上のメニューを開き、**カスタムリポジトリ**を選択します。
2. `https://github.com/leecaochang/Phoenix-MCP` を入力し、カテゴリとして**インテグレーション**を選択して、**追加**をクリックします。
3. HACS のインテグレーション一覧で Phoenix MCP を見つけてインストールし、Home Assistant を再起動します。

手動でインストールする場合は、`custom_components/phoenix_mcp` フォルダーを Home Assistant の設定ディレクトリ内の `custom_components/phoenix_mcp` にコピーしてから、再起動してください。

### セットアップ

**設定 > デバイスとサービス > 統合を追加**を開き、**Phoenix MCP** を検索します。一段階の設定フローを完了したら、サイドバーから **Phoenix MCP** パネルを開き、**エージェントを設定**を選択してください。

## 問題の報告とフィードバック

問題は [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues) で報告してください。
