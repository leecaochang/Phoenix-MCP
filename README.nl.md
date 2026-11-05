# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | [Français](README.fr.md) | [Italiano](README.it.md) | [Deutsch](README.de.md) | **Nederlands** | [Polski](README.pl.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Русский](README.ru.md) | [Svenska](README.sv.md) | [Dansk](README.da.md) | [Norsk Bokmål](README.nb.md) | [Suomi](README.fi.md)

Phoenix MCP geeft je AI-agents veilige, beperkte toegang tot Home Assistant via MCP (Model Context Protocol), een open standaard om AI-apps te verbinden met tools en gegevens. Veiligheid is cruciaal bij het gebruik van AI, en Phoenix MCP heeft vele maanden ontwikkeling en herhaalde beveiligingsbeoordelingen doorlopen. Elke agent heeft een eigen toegangssleutel, beperkt tot alleen de entiteiten en mogelijkheden die je toestaat. Een extra veiligheidslaag (MESA) kan voor een gekozen entiteit je bevestiging vereisen of deze helemaal verbieden, ongeacht welke machtigingen een toegangssleutel heeft gekregen.

Phoenix MCP draait volledig binnen Home Assistant, zonder aparte dienst om uit te rollen. Gebruik de apps die je al hebt, zoals Claude Code, Cursor, ChatGPT of Gemini, of chat direct vanuit Home Assistant met de AI-aanbieder van je keuze, in de cloud of lokaal via Ollama.

Een begeleide configuratie brengt je binnen enkele minuten van installatie naar een werkende agent, ondersteund door een catalogus van 174 tools voor het lezen, bedienen en schrijven van je configuratie. Phoenix MCP gebruikt een vertrouwde en aanpasbare vensterinterface, waardoor je makkelijk begint. Volledige documentatie is ingebouwd en altijd beschikbaar.

## Schermafbeeldingen

![Venster Toegangssleutels met de Machtigingsboom](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/01-access-keys.png)

In **Toegangssleutels** krijgt elke agent een eigen sleutel. Kies een **Persona** en bepaal in de **Machtigingsboom** waar de agent bij kan, tot op één entiteit.

![Goedkeuringsverzoek met een voorbeeld van een dashboardkaart](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/02-approvals.png)

In **Goedkeuringen** bekijk je een dashboardkaart voordat die wordt toegevoegd. Hier vroeg een agent om er een toe te voegen, en er verandert niets totdat je **Goedkeuren** kiest.

![Agent Chat geopend boven een dashboard](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/03-agent-chat.png)

**Agent Chat** is overal in Home Assistant beschikbaar. Hier maakt het een kaart en wacht het op je goedkeuring voordat het iets wijzigt.

![Mijn werkruimte met Agent Chat gekoppeld](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/04-my-workspace.png)

**Mijn werkruimte** bevat de vensters die je het meest gebruikt. Hier staat **Agent Chat** naast **Auditlogboek** en **Wijzigingen**.

## Talen

Het paneel ondersteunt momenteel Engels, Spaans, Frans, Italiaans, Duits, Nederlands, Pools, Russisch, vereenvoudigd Chinees, traditioneel Chinees, Koreaans, Japans, Zweeds, Deens, Noors en Fins. Om de paneeltaal handmatig te wijzigen, open je **Phoenix MCP > Instellingen > Taal** en kies je een taal. Kies **Auto** om weer de taal van je Home Assistant-profiel te volgen.

## Documentatie

De handleiding is ingebouwd in het paneel: open het **Phoenix MCP**-paneel en kies **Gids** in het menu **Help**. Hij legt elk venster en elke instelling uit, wat agents kunnen doen, machtigingen, mogelijkheden, MESA en beveiliging. Hij volgt wat je selecteert, en je kunt erin zoeken.

Voor het eerst hier? Open na de configuratie hieronder het **Phoenix MCP**-paneel en kies **Een agent instellen**. Een wizard leidt je door het maken van een toegangssleutel, het kiezen van wat die mag doen en het verbinden van Agent Chat of je eigen app.

## Vereisten

- Home Assistant 2026.7.0 of nieuwer.
- Eén Phoenix MCP-instantie per Home Assistant. Home Assistant installeert de afhankelijkheden `Babel>=2.18.0` en `jsonschema>=4.26.0` voor je.

## Installeren via de Marktplaats

Op Home Assistant 2026.11 of nieuwer:

1. Ga naar **Instellingen > Marktplaats** en kies **Voeg toe via link**.
2. Voer `https://github.com/leecaochang/Phoenix-MCP` in en kies **Voeg toe**. De eerste keer vraagt de Marktplaats je om GitHub te koppelen.
3. Open Phoenix MCP en kies **Installeren**. Herstart Home Assistant alleen als daarom wordt gevraagd.

Als je upgradet vanaf HACS, verhuist Phoenix MCP vanzelf naar de Marktplaats. Koppel daar GitHub om updates te blijven ontvangen.

## Installeren via HACS

Op Home Assistant 2026.10 of ouder:

1. Open in HACS het menu rechtsboven en kies **Aangepaste repositories**.
2. Voer `https://github.com/leecaochang/Phoenix-MCP` in, selecteer **Integratie** als categorie en klik op **Toevoegen**.
3. Zoek Phoenix MCP in de HACS-integratielijst, installeer het en herstart Home Assistant.

Liever handmatig installeren? Kopieer de map `custom_components/phoenix_mcp` naar de Home Assistant-configuratiemap onder `custom_components/phoenix_mcp` en herstart.

## Instellen

Ga naar **Instellingen > Apparaten en services > Integratie toevoegen** en zoek naar **Phoenix MCP**. Rond de configuratie in één stap af, open vervolgens het **Phoenix MCP**-paneel in de zijbalk en kies **Een agent instellen**.

## Problemen en feedback

Meld problemen op [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues).
