# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | [Français](README.fr.md) | [Italiano](README.it.md) | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | [Polski](README.pl.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | **한국어** | [Русский](README.ru.md) | [Svenska](README.sv.md) | [Dansk](README.da.md) | [Norsk Bokmål](README.nb.md) | [Suomi](README.fi.md)

Phoenix MCP는 AI 앱을 도구 및 데이터에 연결하는 개방형 표준인 MCP(Model Context Protocol)를 통해 AI 에이전트에 안전하고 제한된 Home Assistant 접근을 제공합니다. AI를 사용할 때 보안과 안전은 매우 중요하며, Phoenix MCP는 수개월에 걸친 개발과 보안 검토를 거쳤습니다. 각 에이전트는 고유한 액세스 키를 가지며, 허용한 엔티티와 기능으로만 제한됩니다. 추가 안전 계층([MESA](https://github.com/leecaochang/mesa-core))은 액세스 키에 부여된 권한과 관계없이 선택한 엔티티를 확인 전용 또는 접근 금지로 만들 수 있습니다.

Phoenix MCP는 Home Assistant 내부에서만 실행되므로 별도의 서비스를 배포할 필요가 없습니다. Claude Code, Cursor, ChatGPT, Gemini 등 이미 사용 중인 앱을 쓰거나, 원하는 AI 제공자를 선택해 Home Assistant 안에서 바로 채팅할 수 있습니다. 제공자는 클라우드든 Ollama를 통한 로컬이든 상관없습니다.

안내에 따른 설정으로 설치부터 작동하는 에이전트까지 몇 분 만에 끝낼 수 있으며, 구성을 읽고 제어하고 작성할 수 있는 174개 도구 카탈로그가 뒷받침합니다. Phoenix MCP는 익숙하고 사용자 지정이 가능한 창 기반 인터페이스를 사용하므로 쉽게 시작할 수 있습니다. 전체 문서가 내장되어 있어 언제든지 볼 수 있습니다.

## 스크린샷

![액세스 키 창과 권한 트리](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/01-access-keys.png)

**액세스 키**에서는 에이전트마다 자체 키를 갖습니다. **페르소나**를 선택한 다음 **권한 트리**에서 접근할 수 있는 범위를 단일 엔티티까지 정하세요.

![대시보드 카드 미리 보기가 있는 승인 요청](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/02-approvals.png)

**승인**에서는 대시보드 카드가 추가되기 전에 미리 봅니다. 여기서는 에이전트가 카드 추가를 요청했고, **승인**을 선택하기 전까지는 아무것도 바뀌지 않습니다.

![대시보드 위에 열린 Agent Chat](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/03-agent-chat.png)

**Agent Chat**은 Home Assistant 어디에서나 사용할 수 있습니다. 여기서는 카드를 만든 다음 변경하기 전에 승인을 기다립니다.

![Agent Chat이 도킹된 내 작업 공간](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/04-my-workspace.png)

**내 작업 공간**에는 가장 자주 쓰는 창을 모아 둡니다. 여기서는 **Agent Chat**이 **감사 로그** 및 **변경 내역** 옆에 있습니다.

## 언어

패널은 현재 영어, 스페인어, 프랑스어, 이탈리아어, 독일어, 네덜란드어, 폴란드어, 러시아어, 중국어 간체, 중국어 번체, 한국어, 일본어, 스웨덴어, 덴마크어, 노르웨이어, 핀란드어를 지원합니다. 패널 언어를 수동으로 변경하려면 **Phoenix MCP > 설정 > 언어**를 열고 언어를 선택하세요. **자동**을 선택하면 다시 Home Assistant 프로필 언어를 따릅니다.

## 문서

매뉴얼은 패널에 내장되어 있습니다. **Phoenix MCP** 패널을 열고 **도움말** 메뉴에서 **가이드**를 선택하세요. 모든 창과 설정, 에이전트가 할 수 있는 일, 권한, 기능, MESA, 보안을 설명합니다. 선택한 항목을 따라가며, 검색할 수도 있습니다.

처음이신가요? 아래 설정을 마친 후 **Phoenix MCP** 패널을 열고 **에이전트 설정**을 선택하세요. 마법사가 액세스 키 만들기, 키가 할 수 있는 일 선택, Agent Chat 또는 자신의 앱 연결을 안내합니다.

## 요구 사항

- Home Assistant 2026.7.0 이상.
- Home Assistant마다 Phoenix MCP 인스턴스 하나만 설정할 수 있습니다. Home Assistant가 의존성 `Babel>=2.18.0` 및 `jsonschema>=4.26.0`을 자동으로 설치합니다.

## 마켓플레이스에서 설치

Home Assistant 2026.11 이상:

1. **설정 > 마켓플레이스**로 이동하여 **링크로 추가**를 선택합니다.
2. `https://github.com/leecaochang/Phoenix-MCP`를 입력하고 **추가**를 선택합니다. 처음에는 마켓플레이스가 GitHub 연결을 요청합니다.
3. Phoenix MCP를 열고 **설치**를 선택합니다. Home Assistant를 다시 시작하라는 요청이 있을 때만 다시 시작하세요.

HACS에서 업그레이드하면 Phoenix MCP는 자동으로 마켓플레이스로 옮겨집니다. 계속 업데이트를 받으려면 그곳에서 GitHub를 연결하세요.

## HACS를 통한 설치

Home Assistant 2026.10 이하:

1. HACS에서 오른쪽 위 메뉴를 열고 **사용자 지정 저장소**를 선택합니다.
2. `https://github.com/leecaochang/Phoenix-MCP`를 입력하고 범주로 **통합 구성 요소**를 선택한 다음 **추가**를 클릭하세요.
3. HACS 통합 구성 요소 목록에서 Phoenix MCP를 찾아 설치한 후 Home Assistant를 재시작하세요.

수동으로 설치하고 싶으신가요? `custom_components/phoenix_mcp` 폴더를 Home Assistant 설정 디렉터리의 `custom_components/phoenix_mcp` 아래에 복사한 후 재시작하세요.

## 설정

**설정 > 기기 및 서비스 > 통합 구성 요소 추가**로 이동하여 **Phoenix MCP**를 검색하세요. 단일 단계 설정을 완료한 다음 사이드바에서 **Phoenix MCP** 패널을 열고 **에이전트 설정**을 선택하세요.

## 문제 및 피드백

문제는 [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues)에서 보고해 주세요.
