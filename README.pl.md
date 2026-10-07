# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | [Français](README.fr.md) | [Italiano](README.it.md) | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | **Polski** | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Русский](README.ru.md) | [Svenska](README.sv.md) | [Dansk](README.da.md) | [Norsk Bokmål](README.nb.md) | [Suomi](README.fi.md)

Phoenix MCP daje Twoim agentom AI ograniczony dostęp do Home Assistant na zasadzie najmniejszych uprawnień. Każdy klient otrzymuje własny klucz dostępu, ograniczony dokładnie do encji, które zezwolisz, z własnymi możliwościami, limitem szybkości i opcjonalnym wygaśnięciem. Audyt jest domyślnie włączony i konfigurowalny dla każdego wyniku, każdy klucz dostępu można natychmiast unieważnić, a semantyczna warstwa bezpieczeństwa dla każdej encji (MESA) może sprawić, że urządzenie będzie wymagało potwierdzenia lub będzie niedostępne ze względu na swoją naturę, niezależnie od przyznanych kluczowi dostępu uprawnień. Domyślny tryb MESA to Doradczy: większość ograniczeń zasad tylko ostrzega do czasu wybrania trybu Wymuszony; właściwość tylko do odczytu i jawne odmowy prywatności nadal blokują.

Phoenix MCP działa w całości wewnątrz Home Assistant, bez osobnej usługi do wdrożenia, bez zależności od chmury w głównym serwerze i bez konfiguracji wykraczającej poza panel Phoenix MCP (opcjonalne funkcje Czat agenta, głos i Zadanie AI wysyłają rozmowy do skonfigurowanego dostawcy modelu, chyba że skierujesz je do lokalnego Ollama). Działa z klientami MCP, których już używasz (Claude Code, Cursor, ChatGPT/Codex, Gemini i inne), lub możesz całkowicie pominąć instalowanie klienta i rozmawiać bezpośrednio z Home Assistant. W obu przypadkach konfiguracja z przewodnikiem prowadzi Cię od nowego klucza dostępu do działającego agenta w kilka minut, wspierana przez katalog 169 narzędzi do odczytywania, sterowania i tworzenia Twojej konfiguracji. Panel jest dostępny w wielu językach, domyślnie podąża za językiem Twojego profilu Home Assistant i przełącza się na angielski, gdy ten język jest niedostępny. Home Companion i opcjonalny routing decyzyjny również mogą wysyłać żądania i dozwolone wyniki do hostowanego dostawcy. Zewnętrzne klienty MCP stosują własne ustawienia dostawcy i udostępniania danych.

## Języki

Panel obsługuje obecnie angielski, hiszpański, francuski, włoski, niemiecki, niderlandzki, polski, rosyjski, chiński uproszczony, chiński tradycyjny, koreański, japoński, szwedzki, duński, norweski i fiński. Aby ręcznie zmienić język panelu, otwórz **Phoenix MCP > Ustawienia > Język** i wybierz język. Wybierz **Auto**, aby ponownie podążać za językiem Twojego profilu Home Assistant.

## Dokumentacja

Pełna dokumentacja, w tym spis narzędzi, uprawnienia, możliwości, MESA i API administratora, znajduje się na **[leecaochang.github.io/Phoenix-MCP](https://leecaochang.github.io/Phoenix-MCP/)**. Prowadzą tam linki pomocy w panelu.

Nowy tutaj? Zacznij od **[Szybkiego startu](https://leecaochang.github.io/Phoenix-MCP/quickstart.html)**: tworzy on Twój pierwszy klucz dostępu, przyznaje mu dostęp do jednego urządzenia, a następnie pozwala wybrać, czy chcesz rozmawiać z Home Assistant, czy połączyć zewnętrznego agenta; w obu przypadkach w kilka minut.

## Wymagania

- Home Assistant 2026.7.0 lub nowszy.
- Jedna instancja Phoenix MCP na Home Assistant. Home Assistant instaluje zależności Python `Babel>=2.18.0`, `jsonschema>=4.26.0` zgodnie ze swoimi ograniczeniami zależności; MESA jest dołączona do Phoenix MCP.

## Instalacja przez HACS

1. W HACS otwórz menu w prawym górnym rogu i wybierz **Niestandardowe repozytoria**.
2. Wpisz `https://github.com/leecaochang/Phoenix-MCP`, wybierz **Integracja** jako kategorię, a następnie kliknij **Dodaj**.
3. Znajdź Phoenix MCP na liście integracji HACS, zainstaluj je i uruchom ponownie Home Assistant.

Wolisz zainstalować ręcznie? Skopiuj folder `custom_components/phoenix_mcp` do katalogu konfiguracji Home Assistant w `custom_components/phoenix_mcp`, a następnie uruchom ponownie.

### Konfiguracja

Przejdź do **Ustawienia > Urządzenia i usługi > Dodaj integrację** i wyszukaj **Phoenix MCP**. Przejdź przez jednoetapowy przepływ konfiguracji, a następnie otwórz panel **Phoenix MCP** na pasku bocznym. [Szybki start](https://leecaochang.github.io/Phoenix-MCP/quickstart.html) poprowadzi Cię dalej.

## Problemy i opinie

Zgłaszaj problemy na [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues).
