# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | [Français](README.fr.md) | [Italiano](README.it.md) | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | **Polski** | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Русский](README.ru.md) | [Svenska](README.sv.md) | [Dansk](README.da.md) | [Norsk Bokmål](README.nb.md) | [Suomi](README.fi.md)

Phoenix MCP daje Twoim agentom AI bezpieczny, ograniczony dostęp do Home Assistant przez MCP (Model Context Protocol), otwarty standard łączenia aplikacji AI z narzędziami i danymi. Bezpieczeństwo jest kluczowe podczas korzystania z AI, a Phoenix MCP ma za sobą wiele miesięcy rozwoju i powtarzane przeglądy bezpieczeństwa. Każdy agent ma własny klucz dostępu, ograniczony tylko do encji i możliwości, na które zezwolisz. Dodatkowa warstwa bezpieczeństwa (MESA) może wymagać Twojego potwierdzenia dla wybranej encji lub całkowicie ją zablokować, niezależnie od uprawnień przyznanych kluczowi dostępu.

Phoenix MCP działa w całości wewnątrz Home Assistant, bez osobnej usługi do wdrażania. Używaj aplikacji, które już masz, takich jak Claude Code, Cursor, ChatGPT lub Gemini, albo rozmawiaj bezpośrednio w Home Assistant z wybranym dostawcą AI, w chmurze lub lokalnie przez Ollama.

Konfiguracja z przewodnikiem prowadzi od instalacji do działającego agenta w kilka minut, a wspiera ją katalog 174 narzędzi do odczytu, sterowania i tworzenia konfiguracji. Phoenix MCP ma znajomy i konfigurowalny interfejs okienkowy, który ułatwia start. Pełna dokumentacja jest wbudowana i zawsze dostępna.

## Zrzuty ekranu

![Okno Klucze dostępu z Drzewem uprawnień](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/01-access-keys.png)

W oknie **Klucze dostępu** każdy agent ma własny klucz. Wybierz rolę w oknie **Persona**, a następnie w oknie **Drzewo uprawnień** zdecyduj, do czego agent ma dostęp, aż do pojedynczej encji.

![Prośba o zatwierdzenie z podglądem karty panelu](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/02-approvals.png)

W oknie **Zatwierdzenia** oglądasz podgląd karty panelu, zanim zostanie dodana. Tutaj agent poprosił o dodanie karty i nic się nie zmieni, dopóki nie wybierzesz **Zatwierdź**.

![Agent Chat otwarty nad panelem](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/03-agent-chat.png)

**Agent Chat** jest dostępny wszędzie w Home Assistant. Tutaj tworzy kartę, a potem czeka na Twoje zatwierdzenie, zanim cokolwiek zmieni.

![Mój obszar roboczy z zadokowanym Agent Chat](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/04-my-workspace.png)

**Mój obszar roboczy** zbiera okna, których używasz najczęściej. Tutaj **Agent Chat** znajduje się obok okien **Dziennik audytu** i **Zmiany**.

## Języki

Panel obsługuje obecnie angielski, hiszpański, francuski, włoski, niemiecki, niderlandzki, polski, rosyjski, chiński uproszczony, chiński tradycyjny, koreański, japoński, szwedzki, duński, norweski i fiński. Aby ręcznie zmienić język panelu, otwórz **Phoenix MCP > Ustawienia > Język** i wybierz język. Wybierz **Auto**, aby ponownie podążać za językiem Twojego profilu Home Assistant.

## Dokumentacja

Podręcznik jest wbudowany w panel: otwórz panel **Phoenix MCP** i wybierz **Przewodnik** z menu **Pomoc**. Wyjaśnia każde okno i ustawienie, to, co mogą robić agenci, uprawnienia, możliwości, MESA i bezpieczeństwo. Podąża za tym, co wybierasz, i można go przeszukiwać.

Pierwszy raz tutaj? Po konfiguracji opisanej poniżej otwórz panel **Phoenix MCP** i wybierz **Skonfiguruj agenta**. Kreator przeprowadzi Cię przez utworzenie klucza dostępu, wybór tego, co może robić, i połączenie Agent Chat lub Twojej własnej aplikacji.

## Wymagania

- Home Assistant 2026.7.0 lub nowszy.
- Jedna instancja Phoenix MCP na Home Assistant. Home Assistant sam zainstaluje zależności `Babel>=2.18.0` i `jsonschema>=4.26.0`.

## Instalacja z katalogu społeczności

W Home Assistant 2026.11 lub nowszym:

1. Przejdź do **Ustawienia > Katalog społeczności** i wybierz **Dodaj z linku**.
2. Wpisz `https://github.com/leecaochang/Phoenix-MCP` i wybierz **Utwórz**. Za pierwszym razem katalog poprosi o połączenie z GitHubem.
3. Otwórz Phoenix MCP i wybierz **Zainstaluj**. Uruchom ponownie Home Assistant tylko wtedy, gdy o to poprosi.

Po aktualizacji z HACS Phoenix MCP sam przejdzie do katalogu społeczności. Połącz się tam z GitHubem, aby nadal otrzymywać aktualizacje.

## Instalacja przez HACS

W Home Assistant 2026.10 lub starszym:

1. W HACS otwórz menu w prawym górnym rogu i wybierz **Niestandardowe repozytoria**.
2. Wpisz `https://github.com/leecaochang/Phoenix-MCP`, wybierz **Integracja** jako kategorię, a następnie kliknij **Dodaj**.
3. Znajdź Phoenix MCP na liście integracji HACS, zainstaluj je i uruchom ponownie Home Assistant.

Wolisz zainstalować ręcznie? Skopiuj folder `custom_components/phoenix_mcp` do katalogu konfiguracji Home Assistant w `custom_components/phoenix_mcp`, a następnie uruchom ponownie.

## Konfiguracja

Przejdź do **Ustawienia > Urządzenia i usługi > Dodaj integrację** i wyszukaj **Phoenix MCP**. Zakończ jednoetapową konfigurację, a następnie otwórz panel **Phoenix MCP** na pasku bocznym i wybierz **Skonfiguruj agenta**.

## Problemy i opinie

Zgłaszaj problemy na [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues).
