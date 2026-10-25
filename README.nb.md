# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | [Français](README.fr.md) | [Italiano](README.it.md) | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | [Polski](README.pl.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Русский](README.ru.md) | [Svenska](README.sv.md) | [Dansk](README.da.md) | **Norsk Bokmål** | [Suomi](README.fi.md)

Phoenix MCP gir AI-agentene dine avgrenset tilgang til Home Assistant etter prinsippet om minst mulig tillatelse. Hver klient får sin egen tilgangsnøkkel, begrenset til nøyaktig de entitetene du tillater, med egne kapabiliteter, egen hastighetsgrense og valgfri utløpsdato. Revisjon er aktivert som standard og kan konfigureres per resultat, enhver tilgangsnøkkel kan tilbakekalles umiddelbart, og det semantiske sikkerhetslaget per entitet (MESA) kan gjøre en enhet til kun-bekreft eller helt forbudt ut fra sin art, uansett hvilke tillatelser en tilgangsnøkkel har. Rådgivende er MESA-standardmodusen: de fleste policygrenser advarer til Håndhevet velges; skrivebeskyttet art og uttrykkelige personvernnektelser blokkerer fortsatt.

Phoenix MCP kjører helt inne i Home Assistant, uten en separat tjeneste å distribuere, uten skyavhengighet i kjerneserveren og uten oppsett utover Phoenix MCP-panelet (de valgfrie funksjonene Agent Chat, tale og AI-oppgave sender samtaler til modellleverandøren du konfigurerer, med mindre du peker dem mot en lokal Ollama). Det fungerer med MCP-klientene du allerede bruker (Claude Code, Cursor, ChatGPT/Codex, Gemini med flere), eller du kan hoppe over å installere en klient og chatte inne fra Home Assistant. Uansett tar en veiledet oppsett deg fra en ny tilgangsnøkkel til en fungerende agent på noen minutter, støttet av en katalog med 171 verktøy for å lese, styre og opprette konfigurasjonen din. Panelet finnes på flere språk, følger som standard språket i Home Assistant-profilen din og faller tilbake til engelsk når språket ikke er tilgjengelig. Home Companion og valgfri beslutningsruting kan også sende forespørsler og avgrensede resultater til en hostet leverandør. Eksterne MCP-klienter følger sine egne innstillinger for leverandør og datadeling.

## Språk

Panelet støtter for tiden engelsk, spansk, fransk, italiensk, tysk, nederlandsk, polsk, russisk, forenklet kinesisk, tradisjonell kinesisk, koreansk, japansk, svensk, dansk, norsk og finsk. For å endre panelets språk manuelt, åpne **Phoenix MCP > Innstillinger > Språk** og velg et språk. Velg **Auto** for å følge språket i Home Assistant-profilen din igjen.

## Dokumentasjon

Den fullstendige dokumentasjonen, inkludert verktøyreferansen, tillatelser, kapabiliteter, MESA og admin-API-et, finnes på **[leecaochang.github.io/Phoenix-MCP](https://leecaochang.github.io/Phoenix-MCP/)**. Panelets hjelpelenker peker dit.

Ny her? Start med **[Hurtigstart](https://leecaochang.github.io/Phoenix-MCP/quickstart.html)**: den oppretter din første tilgangsnøkkel, gir den én enhet og lar deg deretter velge om du vil chatte inne fra Home Assistant eller koble til en ekstern agent, uansett på noen minutter.

## Krav

- Home Assistant 2026.7.0 eller nyere.
- Én Phoenix MCP-forekomst per Home Assistant. Home Assistant installerer Python-avhengighetene `Babel>=2.18.0`, `jsonschema>=4.26.0` under sine avhengighetsbegrensninger; MESA er inkludert i Phoenix MCP.

## Installer via HACS

1. Åpne menyen øverst til høyre i HACS, og velg **Egendefinerte repositorier**.
2. Skriv inn `https://github.com/leecaochang/Phoenix-MCP` og velg **Integrasjon** som kategori, og klikk deretter **Legg til**.
3. Finn Phoenix MCP i HACS' integrasjonsliste, installer det og start Home Assistant på nytt.

Vil du heller installere manuelt? Kopier mappen `custom_components/phoenix_mcp` til Home Assistant-konfigurasjonsmappen din under `custom_components/phoenix_mcp`, og start deretter på nytt.

### Sett opp

Gå til **Innstillinger > Enheter og tjenester > Legg til integrasjon** og søk etter **Phoenix MCP**. Klikk deg gjennom konfigurasjonsflyten med ett trinn, og åpne deretter panelet **Phoenix MCP** i sidepanelet. [Hurtigstart](https://leecaochang.github.io/Phoenix-MCP/quickstart.html) tar det derfra.

## Problemer og tilbakemelding

Rapporter problemer på [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues).
