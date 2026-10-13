# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | [Français](README.fr.md) | [Italiano](README.it.md) | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | [Polski](README.pl.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Русский](README.ru.md) | [Svenska](README.sv.md) | **Dansk** | [Norsk Bokmål](README.nb.md) | [Suomi](README.fi.md)

Phoenix MCP giver dine AI-agenter afgrænset adgang til Home Assistant efter princippet om færrest mulige tilladelser. Hver klient får sin egen adgangsnøgle, begrænset til præcis de enheder, du tillader, med egne funktioner, egen hastighedsgrænse og valgfri udløbsdato. Revision er aktiveret som standard og kan konfigureres pr. resultat, enhver adgangsnøgle kan tilbagekaldes øjeblikkeligt, og det semantiske sikkerhedslag pr. entitet (MESA) kan gøre en enhed til kun-bekræft eller helt forbudt ud fra sin art, uanset hvilke tilladelser en adgangsnøgle har. Rådgivende er MESA's standardtilstand: de fleste politikbegrænsninger advarer, indtil Håndhævet vælges; skrivebeskyttet art og udtrykkelige privatlivsnægtelser blokerer stadig.

Phoenix MCP kører udelukkende inde i Home Assistant, uden en separat tjeneste at udrulle, uden cloud-afhængighed i kerneserveren og uden opsætning ud over Phoenix MCP-panelet (de valgfrie funktioner Agent Chat, stemme og AI-opgave sender samtaler til den modeludbyder, du konfigurerer, medmindre du peger dem på en lokal Ollama). Det fungerer med de MCP-klienter, du allerede bruger (Claude Code, Cursor, ChatGPT/Codex, Gemini med flere), eller du kan helt springe installationen af en klient over og chatte inde fra Home Assistant. Uanset hvad fører en guidet opsætning dig fra en ny adgangsnøgle til en fungerende agent på få minutter, understøttet af et katalog med 171 værktøjer til at læse, styre og oprette din konfiguration. Panelet findes på flere sprog, følger som standard sproget i din Home Assistant-profil og falder tilbage til engelsk, når sproget ikke er tilgængeligt. Home Companion og valgfri beslutningsrouting kan også sende anmodninger og afgrænsede resultater til en hostet udbyder. Eksterne MCP-klienter følger deres egne indstillinger for udbyder og datadeling.

## Sprog

Panelet understøtter i øjeblikket engelsk, spansk, fransk, italiensk, tysk, hollandsk, polsk, russisk, forenklet kinesisk, traditionelt kinesisk, koreansk, japansk, svensk, dansk, norsk og finsk. Hvis du vil ændre panelets sprog manuelt, skal du åbne **Phoenix MCP > Indstillinger > Sprog** og vælge et sprog. Vælg **Auto** for at følge sproget i din Home Assistant-profil igen.

## Dokumentation

Den fuldstændige dokumentation, herunder værktøjsreferencen, tilladelser, funktioner, MESA og admin-API'et, findes på **[leecaochang.github.io/Phoenix-MCP](https://leecaochang.github.io/Phoenix-MCP/)**. Panelets hjælpelinks peger dertil.

Ny her? Start med **[Hurtig start](https://leecaochang.github.io/Phoenix-MCP/quickstart.html)**: den opretter din første adgangsnøgle, giver den en enhed og lader dig derefter vælge, om du vil chatte inde fra Home Assistant eller tilslutte en ekstern agent, uanset hvad på få minutter.

## Krav

- Home Assistant 2026.7.0 eller nyere.
- Én Phoenix MCP-instans pr. Home Assistant. Home Assistant installerer Python-afhængighederne `Babel>=2.18.0`, `jsonschema>=4.26.0` under sine afhængighedsbegrænsninger; MESA er inkluderet i Phoenix MCP.

## Installer via HACS

1. Åbn menuen øverst til højre i HACS, og vælg **Brugerdefinerede repositories**.
2. Indtast `https://github.com/leecaochang/Phoenix-MCP`, vælg **Integration** som kategori, og klik derefter på **Tilføj**.
3. Find Phoenix MCP på HACS' integrationsliste, installér det, og genstart Home Assistant.

Vil du hellere installere manuelt? Kopiér mappen `custom_components/phoenix_mcp` til din Home Assistant-konfigurationsmappe under `custom_components/phoenix_mcp`, og genstart derefter.

### Opsætning

Gå til **Indstillinger > Enheder og tjenester > Tilføj integration**, og søg efter **Phoenix MCP**. Klik dig gennem konfigurationsflowet med ét trin, og åbn derefter panelet **Phoenix MCP** i sidepanelet. [Hurtig start](https://leecaochang.github.io/Phoenix-MCP/quickstart.html) tager den derfra.

## Problemer og feedback

Rapportér problemer på [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues).
