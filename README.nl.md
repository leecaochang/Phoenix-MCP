# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | [Français](README.fr.md) | [Italiano](README.it.md) | [Deutsch](README.de.md) | **Nederlands** | [Polski](README.pl.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Русский](README.ru.md) | [Svenska](README.sv.md) | [Dansk](README.da.md) | [Norsk Bokmål](README.nb.md) | [Suomi](README.fi.md)

Phoenix MCP geeft je AI-agents afgebakende toegang tot Home Assistant volgens het principe van minimale rechten. Elke client krijgt een eigen toegangssleutel, beperkt tot precies de entiteiten die je toestaat, met eigen mogelijkheden, snelheidslimiet en optionele verloopdatum. Auditing is standaard ingeschakeld en per uitkomst configureerbaar, elke toegangssleutel kan direct worden ingetrokken, en de semantische veiligheidslaag per entiteit (MESA) kan een apparaat op basis van zijn aard op alleen-bevestigen of verboden zetten, ongeacht de machtigingen die een toegangssleutel krijgt. De standaardmodus van MESA is Adviserend: de meeste beleidsbeperkingen geven alleen waarschuwingen totdat Afgedwongen is geselecteerd; alleen-lezen-eigenschappen en expliciete privacyweigeringen blijven blokkeren.

Phoenix MCP draait volledig binnen Home Assistant, zonder aparte service om uit te rollen, zonder cloudafhankelijkheid in de kernserver en zonder verdere installatie dan het Phoenix MCP-paneel (de optionele functies Agent Chat, spraak en AI-taak sturen gesprekken naar de modelprovider die je configureert, tenzij je ze naar een lokale Ollama verwijst). Het werkt met de MCP-clients die je al gebruikt (Claude Code, Cursor, ChatGPT/Codex, Gemini en andere), of je kunt het installeren van een client helemaal overslaan en rechtstreeks vanuit Home Assistant chatten. Hoe dan ook brengt een begeleide installatie je in enkele minuten van een nieuwe toegangssleutel naar een werkende agent, ondersteund door een catalogus van 174 tools voor het lezen, besturen en opstellen van je configuratie. Het paneel is in meerdere talen beschikbaar, volgt standaard de taal van je Home Assistant-profiel en valt terug op Engels wanneer die taal niet beschikbaar is. Home Companion en optionele beslissingsroutering kunnen ook verzoeken en toegestane resultaten naar een gehoste provider sturen. Externe MCP-clients volgen hun eigen providerinstellingen en instellingen voor het delen van gegevens.

## Talen

Het paneel ondersteunt momenteel Engels, Spaans, Frans, Italiaans, Duits, Nederlands, Pools, Russisch, vereenvoudigd Chinees, traditioneel Chinees, Koreaans, Japans, Zweeds, Deens, Noors en Fins. Om de paneeltaal handmatig te wijzigen, open je **Phoenix MCP > Instellingen > Taal** en kies je een taal. Kies **Auto** om weer de taal van je Home Assistant-profiel te volgen.

## Documentatie

De handleiding is ingebouwd in het paneel: open het **Phoenix MCP**-paneel en kies **Gids** in het menu **Documentatie**. Hij legt elk venster en elke instelling uit, wat agents kunnen doen, machtigingen, mogelijkheden, MESA, beveiliging en de admin-API, en je kunt erin zoeken.

Nieuw hier? Open het **Phoenix MCP**-paneel en kies **Een agent instellen** bij Toegangssleutels. Hiermee maak je je eerste toegangssleutel, bepaal je wat deze mag doen, kun je daarna vanuit Home Assistant chatten of een externe app verbinden, en sluit je af met een test die je kunt volgen.

## Vereisten

- Home Assistant 2026.7.0 of nieuwer.
- Eén Phoenix MCP-instantie per Home Assistant. Home Assistant installeert de Python-afhankelijkheden `Babel>=2.18.0`, `jsonschema>=4.26.0` binnen zijn eigen afhankelijkheidsbeperkingen; MESA is gebundeld met Phoenix MCP.

## Installeren via de Marktplaats

Op Home Assistant 2026.11 of nieuwer:

1. Ga naar **Instellingen > Marktplaats** en kies **Voeg toe via link**.
2. Voer `https://github.com/leecaochang/Phoenix-MCP` in en kies **Voeg toe**. De eerste keer vraagt de Marktplaats je om GitHub te koppelen.
3. Open Phoenix MCP, kies **Installeren** en herstart Home Assistant.

Als je upgradet vanaf HACS, verhuist Phoenix MCP vanzelf naar de Marktplaats. Koppel daar GitHub om updates te blijven ontvangen.

## Installeren via HACS

Op Home Assistant 2026.10 of ouder:

1. Open in HACS het menu rechtsboven en kies **Aangepaste repositories**.
2. Voer `https://github.com/leecaochang/Phoenix-MCP` in, selecteer **Integratie** als categorie en klik op **Toevoegen**.
3. Zoek Phoenix MCP in de HACS-integratielijst, installeer het en herstart Home Assistant.

Liever handmatig installeren? Kopieer de map `custom_components/phoenix_mcp` naar de Home Assistant-configuratiemap onder `custom_components/phoenix_mcp` en herstart.

### Instellen

Ga naar **Instellingen > Apparaten en services > Integratie toevoegen** en zoek naar **Phoenix MCP**. Doorloop de configuratiestap, open vervolgens het **Phoenix MCP**-paneel in de zijbalk en kies **Een agent instellen**.

## Problemen en feedback

Meld problemen op [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues).
