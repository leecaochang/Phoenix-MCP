# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | [Français](README.fr.md) | [Italiano](README.it.md) | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | [Polski](README.pl.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Русский](README.ru.md) | [Svenska](README.sv.md) | **Dansk** | [Norsk Bokmål](README.nb.md) | [Suomi](README.fi.md)

Phoenix MCP giver dine AI-agenter sikker, begrænset adgang til Home Assistant gennem MCP (Model Context Protocol), en åben standard til at forbinde AI-apps med værktøjer og data. Sikkerhed er afgørende, når du bruger AI, og Phoenix MCP har gennemgået mange måneders udvikling og sikkerhedsgennemgange. Hver agent har sin egen adgangsnøgle, begrænset til kun de entiteter og funktioner, du tillader. Et ekstra sikkerhedslag ([MESA](https://github.com/leecaochang/mesa-core)) kan kræve din bekræftelse for en valgt entitet eller gøre den helt forbudt, uanset hvilke tilladelser en adgangsnøgle har fået.

Phoenix MCP kører helt inde i Home Assistant, uden en separat tjeneste, der skal installeres. Brug de apps, du allerede har, som Claude Code, Cursor, ChatGPT eller Gemini, eller chat direkte i Home Assistant med den AI-udbyder, du vælger, i skyen eller lokalt via Ollama.

En guidet opsætning fører dig fra installation til en fungerende agent på få minutter, understøttet af et katalog med 174 værktøjer til at læse, styre og redigere din konfiguration. Phoenix MCP har en velkendt og tilpasselig vinduesbaseret brugerflade, der gør det let at komme i gang. Fuld dokumentation er indbygget og altid tilgængelig.

## Skærmbilleder

![Vinduet Adgangsnøgler med Tilladelsestræ](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/01-access-keys.png)

I **Adgangsnøgler** får hver agent sin egen nøgle. Vælg en **Persona**, og bestem derefter i **Tilladelsestræ**, hvad agenten kan nå, helt ned til en enkelt entitet.

![Godkendelsesanmodning med forhåndsvisning af et kort til betjeningspanelet](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/02-approvals.png)

I **Godkendelser** får du vist et kort til betjeningspanelet, før det tilføjes. Her bad en agent om at tilføje et, og intet ændres, før du vælger **Godkend**.

![Agent Chat åbnet oven på et betjeningspanel](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/03-agent-chat.png)

**Agent Chat** er tilgængelig overalt i Home Assistant. Her opretter den et kort og venter derefter på din godkendelse, før noget ændres.

![Mit arbejdsområde med Agent Chat forankret](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/04-my-workspace.png)

**Mit arbejdsområde** samler de vinduer, du bruger mest. Her står **Agent Chat** ved siden af **Revisionslog** og **Ændringer**.

## Sprog

Panelet understøtter i øjeblikket engelsk, spansk, fransk, italiensk, tysk, hollandsk, polsk, russisk, forenklet kinesisk, traditionelt kinesisk, koreansk, japansk, svensk, dansk, norsk og finsk. Hvis du vil ændre panelets sprog manuelt, skal du åbne **Phoenix MCP > Indstillinger > Sprog** og vælge et sprog. Vælg **Auto** for at følge sproget i din Home Assistant-profil igen.

## Dokumentation

Manualen er indbygget i panelet: åbn panelet **Phoenix MCP**, og vælg **Guide** i menuen **Hjælp**. Den forklarer hvert vindue og hver indstilling, hvad agenter kan gøre, tilladelser, funktioner, MESA og sikkerhed. Den følger det, du vælger, og du kan søge i den.

Første gang her? Efter opsætningen nedenfor skal du åbne panelet **Phoenix MCP** og vælge **Opsæt en agent**. En guide fører dig gennem at oprette en adgangsnøgle, vælge, hvad den må, og tilslutte Agent Chat eller din egen app.

## Krav

- Home Assistant 2026.7.0 eller nyere.
- Én Phoenix MCP-instans pr. Home Assistant. Home Assistant installerer afhængighederne `Babel>=2.18.0` og `jsonschema>=4.26.0` for dig.

## Installer fra Markedspladsen

På Home Assistant 2026.11 eller nyere:

1. Gå til **Indstillinger > Markedspladsen**, og vælg **Tilføj fra link**.
2. Indtast `https://github.com/leecaochang/Phoenix-MCP`, og vælg **Tilføj**. Første gang beder Markedspladsen dig om at forbinde til GitHub.
3. Åbn Phoenix MCP, og vælg **Installér**. Genstart kun Home Assistant, hvis det beder dig om det.

Hvis du opgraderer fra HACS, flytter Phoenix MCP selv til Markedspladsen. Forbind til GitHub dér for at blive ved med at få opdateringer.

## Installer via HACS

På Home Assistant 2026.10 eller ældre:

1. Åbn menuen øverst til højre i HACS, og vælg **Brugerdefinerede repositories**.
2. Indtast `https://github.com/leecaochang/Phoenix-MCP`, vælg **Integration** som kategori, og klik derefter på **Tilføj**.
3. Find Phoenix MCP på HACS' integrationsliste, installér det, og genstart Home Assistant.

Vil du hellere installere manuelt? Kopiér mappen `custom_components/phoenix_mcp` til din Home Assistant-konfigurationsmappe under `custom_components/phoenix_mcp`, og genstart derefter.

## Opsætning

Gå til **Indstillinger > Enheder og tjenester > Tilføj integration**, og søg efter **Phoenix MCP**. Gennemfør opsætningen med ét trin, og åbn derefter panelet **Phoenix MCP** i sidepanelet, og vælg **Opsæt en agent**.

## Problemer og feedback

Rapportér problemer på [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues).
