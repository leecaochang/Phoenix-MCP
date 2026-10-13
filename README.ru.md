# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | [Français](README.fr.md) | [Italiano](README.it.md) | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | [Polski](README.pl.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | **Русский** | [Svenska](README.sv.md) | [Dansk](README.da.md) | [Norsk Bokmål](README.nb.md) | [Suomi](README.fi.md)

Phoenix MCP даёт вашим ИИ-агентам ограниченный доступ к Home Assistant по принципу минимальных привилегий. Каждый клиент получает собственный ключ доступа, ограниченный ровно теми сущностями, которые вы разрешите, с собственными возможностями, ограничением частоты и необязательным сроком действия. Аудит включён по умолчанию и настраивается по каждому результату, любой ключ доступа можно мгновенно отозвать, а слой семантической безопасности для каждой сущности (MESA) может сделать устройство доступным только с подтверждением или полностью запрещённым по его природе, независимо от выданных ключу доступа разрешений. По умолчанию MESA работает в рекомендательном режиме: большинство ограничений только предупреждают до выбора принудительного режима; свойства только для чтения и явные запреты конфиденциальности продолжают блокировать.

Phoenix MCP полностью работает внутри Home Assistant: без отдельной службы для развёртывания, без облачной зависимости в основном сервере и без настройки, кроме панели Phoenix MCP (необязательные функции «Чат с агентом», голос и AI-задача отправляют диалоги настроенному вами провайдеру моделей, если вы не укажете локальный Ollama). Он работает с MCP-клиентами, которые вы уже используете (Claude Code, Cursor, ChatGPT/Codex, Gemini и другие), или можно вовсе не устанавливать клиент и общаться прямо из Home Assistant. В любом случае управляемая настройка за несколько минут проведёт вас от нового ключа доступа к работающему агенту, опираясь на каталог из 171 инструментов для чтения, управления и создания конфигурации. Панель доступна на нескольких языках, по умолчанию следует языку вашего профиля Home Assistant и переключается на английский, когда этот язык недоступен. Home Companion и необязательная маршрутизация решений также могут отправлять запросы и разрешённые результаты размещённому провайдеру. Внешние MCP-клиенты используют собственные настройки провайдера и передачи данных.

## Языки

Сейчас панель поддерживает английский, испанский, французский, итальянский, немецкий, нидерландский, польский, русский, упрощённый китайский, традиционный китайский, корейский, японский, шведский, датский, норвежский и финский. Чтобы изменить язык панели вручную, откройте **Phoenix MCP > Настройки > Язык** и выберите язык. Выберите **Авто**, чтобы снова следовать языку вашего профиля Home Assistant.

## Документация

Полная документация, включая справочник инструментов, разрешения, возможности, MESA и административный API, находится по адресу **[leecaochang.github.io/Phoenix-MCP](https://leecaochang.github.io/Phoenix-MCP/)**. Ссылки справки в панели ведут туда.

Впервые здесь? Начните с **[Быстрого старта](https://leecaochang.github.io/Phoenix-MCP/quickstart.html)**: он создаёт ваш первый ключ доступа, предоставляет ему доступ к одному устройству, а затем позволяет выбрать, общаться ли из Home Assistant или подключить внешнего агента. В обоих случаях это занимает несколько минут.

## Требования

- Home Assistant 2026.7.0 или новее.
- Один экземпляр Phoenix MCP на Home Assistant. Home Assistant устанавливает зависимости Python `Babel>=2.18.0`, `jsonschema>=4.26.0` с учётом своих ограничений зависимостей; MESA включена в Phoenix MCP.

## Установка через HACS

1. В HACS откройте меню в правом верхнем углу и выберите **Пользовательские репозитории**.
2. Введите `https://github.com/leecaochang/Phoenix-MCP`, выберите категорию **Интеграция** и нажмите **Добавить**.
3. Найдите Phoenix MCP в списке интеграций HACS, установите его и перезапустите Home Assistant.

Предпочитаете установить вручную? Скопируйте папку `custom_components/phoenix_mcp` в каталог конфигурации Home Assistant в `custom_components/phoenix_mcp`, затем перезапустите.

### Настройка

Перейдите в **Настройки > Устройства и службы > Добавить интеграцию** и найдите **Phoenix MCP**. Пройдите одношаговый поток настройки, затем откройте панель **Phoenix MCP** в боковой панели. Дальше вас проведёт [Быстрый старт](https://leecaochang.github.io/Phoenix-MCP/quickstart.html).

## Проблемы и отзывы

Сообщайте о проблемах на [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues).
