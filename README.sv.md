# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | [Français](README.fr.md) | [Italiano](README.it.md) | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | [Polski](README.pl.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Русский](README.ru.md) | **Svenska** | [Dansk](README.da.md) | [Norsk Bokmål](README.nb.md) | [Suomi](README.fi.md)

Phoenix MCP ger dina AI-agenter avgränsad åtkomst till Home Assistant enligt principen om lägsta behörighet. Varje klient får en egen åtkomstnyckel, begränsad till exakt de entiteter du tillåter, med egna funktioner, egen hastighetsbegränsning och valfritt utgångsdatum. Revision är aktiverad som standard och kan konfigureras per utfall, varje åtkomstnyckel kan återkallas omedelbart, och det semantiska säkerhetslagret per entitet (MESA) kan göra en enhet till endast-bekräfta eller helt förbjuden utifrån sin art, oavsett vilka behörigheter en åtkomstnyckel har. Rådgivande är MESA:s standardläge: de flesta policybegränsningar varnar tills Påtvingat väljs; skrivskyddad art och uttryckliga integritetsnekanden blockerar fortfarande.

Phoenix MCP körs helt inuti Home Assistant, utan separat tjänst att driftsätta, utan molnberoende i kärnservern och utan konfiguration utöver panelen Phoenix MCP (de valfria funktionerna Agent Chat, röst och AI-uppgift skickar konversationer till den modellleverantör du konfigurerar, om du inte pekar dem mot en lokal Ollama). Det fungerar med de MCP-klienter du redan använder (Claude Code, Cursor, ChatGPT/Codex, Gemini med flera), eller så kan du hoppa över att installera en klient och chatta inifrån Home Assistant. Hur du än gör tar en guidad konfiguration dig från en ny åtkomstnyckel till en fungerande agent på några minuter, med stöd av en katalog med 169 verktyg för att läsa, styra och skapa din konfiguration. Panelen finns på flera språk, följer språket i din Home Assistant-profil som standard och faller tillbaka på engelska när språket inte är tillgängligt. Home Companion och valfri beslutsdirigering kan också skicka begäranden och avgränsade resultat till en extern leverantör. Externa MCP-klienter följer sina egna inställningar för leverantör och datautbyte.

## Språk

Panelen stöder för närvarande engelska, spanska, franska, italienska, tyska, nederländska, polska, ryska, förenklad kinesiska, traditionell kinesiska, koreanska, japanska, svenska, danska, norska och finska. Om du vill ändra panelens språk manuellt öppnar du **Phoenix MCP > Inställningar > Språk** och väljer ett språk. Välj **Auto** för att åter följa språket i din Home Assistant-profil.

## Dokumentation

Den fullständiga dokumentationen, inklusive verktygsreferensen, behörigheter, funktioner, MESA och admin-API:t, finns på **[leecaochang.github.io/Phoenix-MCP](https://leecaochang.github.io/Phoenix-MCP/)**. Panelens hjälplänkar pekar dit.

Ny här? Börja med **[Snabbstart](https://leecaochang.github.io/Phoenix-MCP/quickstart.html)**: den skapar din första åtkomstnyckel, ger den en enhet och låter dig sedan välja om du vill chatta inifrån Home Assistant eller ansluta en extern agent, på några minuter hur du än gör.

## Krav

- Home Assistant 2026.7.0 eller senare.
- En Phoenix MCP-instans per Home Assistant. Home Assistant installerar Python-beroendena `Babel>=2.18.0`, `jsonschema>=4.26.0` inom sina beroendebegränsningar; MESA ingår i Phoenix MCP.

## Installera via HACS

1. Öppna menyn uppe till höger i HACS och välj **Anpassade kodförråd**.
2. Ange `https://github.com/leecaochang/Phoenix-MCP` och välj **Integration** som kategori och klicka sedan på **Lägg till**.
3. Leta upp Phoenix MCP i HACS integrationslista, installera det och starta om Home Assistant.

Föredrar du att installera manuellt? Kopiera mappen `custom_components/phoenix_mcp` till din Home Assistant-konfigurationskatalog under `custom_components/phoenix_mcp` och starta sedan om.

### Konfigurera

Gå till **Inställningar > Enheter och tjänster > Lägg till integration** och sök efter **Phoenix MCP**. Klicka igenom konfigurationsflödet med ett steg och öppna sedan panelen **Phoenix MCP** i sidofältet. [Snabbstart](https://leecaochang.github.io/Phoenix-MCP/quickstart.html) tar det därifrån.

## Problem och återkoppling

Rapportera problem på [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues).
