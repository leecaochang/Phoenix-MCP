# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | [Français](README.fr.md) | [Italiano](README.it.md) | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | [Polski](README.pl.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | **Русский** | [Svenska](README.sv.md) | [Dansk](README.da.md) | [Norsk Bokmål](README.nb.md) | [Suomi](README.fi.md)

Phoenix MCP даёт вашим ИИ-агентам безопасный, ограниченный доступ к Home Assistant через MCP (Model Context Protocol), открытый стандарт подключения ИИ-приложений к инструментам и данным. Безопасность крайне важна при использовании ИИ, и Phoenix MCP прошёл через многие месяцы разработки и проверки безопасности. У каждого агента свой ключ доступа, ограниченный только теми объектами и возможностями, которые вы разрешите. Дополнительный уровень безопасности ([MESA](https://github.com/leecaochang/mesa-core)) может потребовать вашего подтверждения для выбранного объекта или полностью запретить доступ к нему, какие бы разрешения ни были выданы ключу доступа.

Phoenix MCP работает полностью внутри Home Assistant, без отдельного сервиса, который нужно разворачивать. Используйте приложения, которые у вас уже есть, например Claude Code, Cursor, ChatGPT или Gemini, или общайтесь прямо в Home Assistant с выбранным поставщиком ИИ, в облаке или локально через Ollama.

Пошаговая настройка проведёт вас от установки до работающего агента за считаные минуты, а каталог из 174 инструментов позволит читать, контролировать и создавать вашу конфигурацию. Phoenix MCP использует привычный настраиваемый оконный интерфейс, с которым легко начать работу. Полная документация встроена и доступна в любой момент.

## Снимки экрана

![Окно «Ключи доступа» с деревом разрешений](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/01-access-keys.png)

В разделе **Ключи доступа** у каждого агента свой ключ. Выберите персону в окне **Персона**, затем в окне **Дерево разрешений** определите, к чему у агента есть доступ, вплоть до отдельного объекта.

![Запрос на одобрение с предпросмотром карточки панели управления](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/02-approvals.png)

В разделе **Одобрения** вы заранее видите карточку панели управления до её добавления. Здесь агент попросил добавить карточку, и ничего не изменится, пока вы не выберете **Одобрить**.

![Agent Chat поверх панели управления](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/03-agent-chat.png)

**Agent Chat** доступен везде в Home Assistant. Здесь он создаёт карточку и ждёт вашего одобрения, прежде чем что-либо менять.

![Моя рабочая область с закреплённым Agent Chat](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/04-my-workspace.png)

**Моя рабочая область** объединяет окна, которыми вы пользуетесь чаще всего. Здесь **Agent Chat** расположен рядом с окнами **Журнал аудита** и **Изменения**.

## Языки

Сейчас панель поддерживает английский, испанский, французский, итальянский, немецкий, нидерландский, польский, русский, упрощённый китайский, традиционный китайский, корейский, японский, шведский, датский, норвежский и финский. Чтобы изменить язык панели вручную, откройте **Phoenix MCP > Настройки > Язык** и выберите язык. Выберите **Авто**, чтобы снова следовать языку вашего профиля Home Assistant.

## Документация

Справка встроена в панель: откройте панель **Phoenix MCP** и выберите **Руководство** в меню **Справка**. Она объясняет каждое окно и каждую настройку, что могут делать агенты, разрешения, возможности, MESA и безопасность. Она следует за вашим выбором и поддерживает поиск.

Впервые здесь? После настройки, описанной ниже, откройте панель **Phoenix MCP** и выберите **Настроить агента**. Мастер проведёт вас через создание ключа доступа, выбор того, что ему разрешено, и подключение Agent Chat или вашего собственного приложения.

## Требования

- Home Assistant 2026.7.0 или новее.
- Один экземпляр Phoenix MCP на Home Assistant. Home Assistant сам установит зависимости `Babel>=2.18.0` и `jsonschema>=4.26.0`.

## Установка из Marketplace

В Home Assistant 2026.11 или новее:

1. Откройте **Настройки > Marketplace** и выберите **Добавить по ссылке**.
2. Введите `https://github.com/leecaochang/Phoenix-MCP` и выберите **Добавить**. При первом добавлении Marketplace попросит подключить GitHub.
3. Откройте Phoenix MCP и выберите **Установить**. Перезапускайте Home Assistant, только если он об этом попросит.

При обновлении с HACS Phoenix MCP сам перейдёт в Marketplace. Подключите там GitHub, чтобы и дальше получать обновления.

## Установка через HACS

В Home Assistant 2026.10 или старше:

1. В HACS откройте меню в правом верхнем углу и выберите **Пользовательские репозитории**.
2. Введите `https://github.com/leecaochang/Phoenix-MCP`, выберите категорию **Интеграция** и нажмите **Добавить**.
3. Найдите Phoenix MCP в списке интеграций HACS, установите его и перезапустите Home Assistant.

Предпочитаете установить вручную? Скопируйте папку `custom_components/phoenix_mcp` в каталог конфигурации Home Assistant в `custom_components/phoenix_mcp`, затем перезапустите.

## Настройка

Перейдите в **Настройки > Устройства и службы > Добавить интеграцию** и найдите **Phoenix MCP**. Завершите одношаговую настройку, затем откройте панель **Phoenix MCP** в боковой панели и выберите **Настроить агента**.

## Проблемы и отзывы

Сообщайте о проблемах на [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues).
