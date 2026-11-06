# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | [Français](README.fr.md) | [Italiano](README.it.md) | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | [Polski](README.pl.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Русский](README.ru.md) | **Svenska** | [Dansk](README.da.md) | [Norsk Bokmål](README.nb.md) | [Suomi](README.fi.md)

Phoenix MCP ger dina AI-agenter säker, begränsad åtkomst till Home Assistant via MCP (Model Context Protocol), en öppen standard för att koppla AI-appar till verktyg och data. Säkerhet är avgörande när du använder AI, och Phoenix MCP har genomgått många månaders utveckling och säkerhetsgranskningar. Varje agent har en egen åtkomstnyckel, begränsad till bara de entiteter och funktioner du tillåter. Ett extra säkerhetslager (MESA) kan kräva din bekräftelse för en vald entitet eller göra den helt förbjuden, oavsett vilka behörigheter en åtkomstnyckel har fått.

Phoenix MCP körs helt inuti Home Assistant, utan någon separat tjänst att driftsätta. Använd apparna du redan har, som Claude Code, Cursor, ChatGPT eller Gemini, eller chatta direkt i Home Assistant med den AI-leverantör du väljer, i molnet eller lokalt via Ollama.

En guidad konfiguration tar dig från installation till en fungerande agent på några minuter, med stöd av en katalog med 174 verktyg för att läsa, styra och skriva din konfiguration. Phoenix MCP använder ett välbekant och anpassningsbart fönsterbaserat gränssnitt som gör det enkelt att komma igång. Fullständig dokumentation är inbyggd och alltid tillgänglig.

## Skärmbilder

![Fönstret Åtkomstnycklar med Behörighetsträd](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/01-access-keys.png)

I **Åtkomstnycklar** får varje agent en egen nyckel. Välj en **Persona** och bestäm sedan i **Behörighetsträd** vad agenten kommer åt, ner till en enskild entitet.

![Godkännandebegäran med förhandsgranskning av ett kort på kontrollpanelen](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/02-approvals.png)

I **Godkännanden** förhandsgranskar du ett kort för kontrollpanelen innan det läggs till. Här bad en agent om att få lägga till ett, och inget ändras förrän du väljer **Godkänn**.

![Agent Chat öppen ovanpå en kontrollpanel](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/03-agent-chat.png)

**Agent Chat** är tillgänglig överallt i Home Assistant. Här skapar den ett kort och väntar sedan på ditt godkännande innan något ändras.

![Min arbetsyta med Agent Chat dockad](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/04-my-workspace.png)

**Min arbetsyta** samlar de fönster du använder mest. Här ligger **Agent Chat** bredvid **Revisionslogg** och **Ändringar**.

## Språk

Panelen stöder för närvarande engelska, spanska, franska, italienska, tyska, nederländska, polska, ryska, förenklad kinesiska, traditionell kinesiska, koreanska, japanska, svenska, danska, norska och finska. Om du vill ändra panelens språk manuellt öppnar du **Phoenix MCP > Inställningar > Språk** och väljer ett språk. Välj **Auto** för att åter följa språket i din Home Assistant-profil.

## Dokumentation

Handboken är inbyggd i panelen: öppna panelen **Phoenix MCP** och välj **Guide** i menyn **Hjälp**. Den förklarar varje fönster och inställning, vad agenter kan göra, behörigheter, funktioner, MESA och säkerhet. Den följer det du väljer, och du kan söka i den.

Första gången här? Efter konfigurationen nedan öppnar du panelen **Phoenix MCP** och väljer **Konfigurera en agent**. En guide tar dig igenom att skapa en åtkomstnyckel, välja vad den får göra och ansluta Agent Chat eller din egen app.

## Krav

- Home Assistant 2026.7.0 eller senare.
- En Phoenix MCP-instans per Home Assistant. Home Assistant installerar beroendena `Babel>=2.18.0` och `jsonschema>=4.26.0` åt dig.

## Installera från Marknadsplatsen

På Home Assistant 2026.11 eller senare:

1. Gå till **Inställningar > Marknadsplatsen** och välj **Lägg till från länk**.
2. Ange `https://github.com/leecaochang/Phoenix-MCP` och välj **Lägg till**. Första gången ber Marknadsplatsen dig att ansluta GitHub.
3. Öppna Phoenix MCP och välj **Installera**. Starta bara om Home Assistant om det ber dig.

Om du uppgraderar från HACS flyttar Phoenix MCP själv till Marknadsplatsen. Anslut GitHub där för att fortsätta få uppdateringar.

## Installera via HACS

På Home Assistant 2026.10 eller äldre:

1. Öppna menyn uppe till höger i HACS och välj **Anpassade kodförråd**.
2. Ange `https://github.com/leecaochang/Phoenix-MCP` och välj **Integration** som kategori och klicka sedan på **Lägg till**.
3. Leta upp Phoenix MCP i HACS integrationslista, installera det och starta om Home Assistant.

Föredrar du att installera manuellt? Kopiera mappen `custom_components/phoenix_mcp` till din Home Assistant-konfigurationskatalog under `custom_components/phoenix_mcp` och starta sedan om.

## Konfigurera

Gå till **Inställningar > Enheter och tjänster > Lägg till integration** och sök efter **Phoenix MCP**. Slutför konfigurationen med ett steg, öppna sedan panelen **Phoenix MCP** i sidofältet och välj **Konfigurera en agent**.

## Problem och återkoppling

Rapportera problem på [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues).
