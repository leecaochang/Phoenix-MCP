# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | [Français](README.fr.md) | [Italiano](README.it.md) | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | [Polski](README.pl.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Русский](README.ru.md) | [Svenska](README.sv.md) | [Dansk](README.da.md) | **Norsk Bokmål** | [Suomi](README.fi.md)

Phoenix MCP gir AI-agentene dine sikker, begrenset tilgang til Home Assistant gjennom MCP (Model Context Protocol), en åpen standard for å koble AI-apper til verktøy og data. Sikkerhet er avgjørende når du bruker AI, og Phoenix MCP har gjennomgått mange måneder med utvikling og sikkerhetsgjennomganger. Hver agent har sin egen tilgangsnøkkel, begrenset til bare de entitetene og kapabilitetene du tillater. Et ekstra sikkerhetslag ([MESA](https://github.com/leecaochang/mesa-core)) kan kreve bekreftelsen din for en valgt entitet eller gjøre den helt forbudt, uansett hvilke tillatelser en tilgangsnøkkel har fått.

Phoenix MCP kjører helt inne i Home Assistant, uten noen separat tjeneste å distribuere. Bruk appene du allerede har, som Claude Code, Cursor, ChatGPT eller Gemini, eller chat direkte i Home Assistant med AI-leverandøren du velger, i skyen eller lokalt via Ollama.

Et veiledet oppsett tar deg fra installasjon til en fungerende agent på få minutter, støttet av en katalog med 174 verktøy for å lese, styre og redigere konfigurasjonen din. Phoenix MCP bruker et kjent og tilpassbart vindusbasert grensesnitt som gjør det enkelt å komme i gang. Full dokumentasjon er innebygd og alltid tilgjengelig.

## Skjermbilder

![Vinduet Tilgangsnøkler med Tillatelsestre](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/01-access-keys.png)

I **Tilgangsnøkler** får hver agent sin egen nøkkel. Velg en **Persona**, og bestem deretter i **Tillatelsestre** hva agenten kan nå, helt ned til én enkelt entitet.

![Godkjenningsforespørsel med forhåndsvisning av et dashbordkort](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/02-approvals.png)

I **Godkjenninger** forhåndsviser du et dashbordkort før det legges til. Her ba en agent om å legge til et, og ingenting endres før du velger **Godkjenn**.

![Agent Chat åpnet over et dashbord](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/03-agent-chat.png)

**Agent Chat** er tilgjengelig overalt i Home Assistant. Her lager den et kort og venter deretter på godkjenningen din før noe endres.

![Mitt arbeidsområde med Agent Chat forankret](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/04-my-workspace.png)

**Mitt arbeidsområde** samler vinduene du bruker mest. Her står **Agent Chat** ved siden av **Revisjonslogg** og **Endringer**.

## Språk

Panelet støtter for tiden engelsk, spansk, fransk, italiensk, tysk, nederlandsk, polsk, russisk, forenklet kinesisk, tradisjonell kinesisk, koreansk, japansk, svensk, dansk, norsk og finsk. For å endre panelets språk manuelt, åpne **Phoenix MCP > Innstillinger > Språk** og velg et språk. Velg **Auto** for å følge språket i Home Assistant-profilen din igjen.

## Dokumentasjon

Håndboken er innebygd i panelet: åpne panelet **Phoenix MCP** og velg **Veiledning** fra menyen **Hjelp**. Den forklarer hvert vindu og hver innstilling, hva agenter kan gjøre, tillatelser, kapabiliteter, MESA og sikkerhet. Den følger det du velger, og du kan søke i den.

Første gang her? Etter oppsettet nedenfor åpner du panelet **Phoenix MCP** og velger **Sett opp en agent**. En veiviser tar deg gjennom å opprette en tilgangsnøkkel, velge hva den kan gjøre, og koble til Agent Chat eller din egen app.

## Krav

- Home Assistant 2026.7.0 eller nyere.
- Én Phoenix MCP-forekomst per Home Assistant. Home Assistant installerer avhengighetene `Babel>=2.18.0` og `jsonschema>=4.26.0` for deg.

## Installer fra Markedsplassen

På Home Assistant 2026.11 eller nyere:

1. Gå til **Innstillinger > Markedsplassen**, og velg **Legg til fra lenke**.
2. Skriv inn `https://github.com/leecaochang/Phoenix-MCP`, og velg **Legg til**. Første gang ber Markedsplassen deg om å koble til GitHub.
3. Åpne Phoenix MCP og velg **Installer**. Start Home Assistant på nytt bare hvis det ber deg om det.

Hvis du oppgraderer fra HACS, flytter Phoenix MCP seg selv til Markedsplassen. Koble til GitHub der for å fortsette å få oppdateringer.

## Installer via HACS

På Home Assistant 2026.10 eller eldre:

1. Åpne menyen øverst til høyre i HACS, og velg **Egendefinerte repositorier**.
2. Skriv inn `https://github.com/leecaochang/Phoenix-MCP` og velg **Integrasjon** som kategori, og klikk deretter **Legg til**.
3. Finn Phoenix MCP i HACS' integrasjonsliste, installer det og start Home Assistant på nytt.

Vil du heller installere manuelt? Kopier mappen `custom_components/phoenix_mcp` til Home Assistant-konfigurasjonsmappen din under `custom_components/phoenix_mcp`, og start deretter på nytt.

## Sett opp

Gå til **Innstillinger > Enheter og tjenester > Legg til integrasjon** og søk etter **Phoenix MCP**. Fullfør oppsettet med ett trinn, åpne deretter panelet **Phoenix MCP** i sidepanelet og velg **Sett opp en agent**.

## Problemer og tilbakemelding

Rapporter problemer på [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues).
