# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | [Français](README.fr.md) | [Italiano](README.it.md) | **Deutsch** | [Nederlands](README.nl.md) | [Polski](README.pl.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Русский](README.ru.md) | [Svenska](README.sv.md) | [Dansk](README.da.md) | [Norsk Bokmål](README.nb.md) | [Suomi](README.fi.md)

Phoenix MCP gibt Ihren KI-Agenten sicheren, begrenzten Zugriff auf Home Assistant über MCP (Model Context Protocol), einen offenen Standard, der KI-Apps mit Werkzeugen und Daten verbindet. Sicherheit ist beim Einsatz von KI entscheidend, und Phoenix MCP hat viele Monate Entwicklung und wiederholte Sicherheitsprüfungen durchlaufen. Jeder Agent hat seinen eigenen Zugriffsschlüssel, der nur auf die von Ihnen erlaubten Entitäten und Fähigkeiten beschränkt ist. Eine zusätzliche Sicherheitsebene (MESA) kann für eine ausgewählte Entität Ihre Bestätigung verlangen oder sie ganz sperren, unabhängig davon, welche Berechtigungen ein Zugriffsschlüssel hat.

Phoenix MCP läuft vollständig in Home Assistant, ohne separaten Dienst, der bereitgestellt werden muss. Nutzen Sie die Apps, die Sie bereits haben, etwa Claude Code, Cursor, ChatGPT oder Gemini, oder chatten Sie direkt in Home Assistant mit dem KI-Anbieter Ihrer Wahl, in der Cloud oder lokal über Ollama.

Eine geführte Einrichtung bringt Sie in wenigen Minuten von der Installation zu einem funktionierenden Agenten, unterstützt von einem Katalog mit 174 Werkzeugen zum Lesen, Steuern und Bearbeiten Ihrer Konfiguration. Phoenix MCP hat eine vertraute und anpassbare Fensteroberfläche, die den Einstieg leicht macht. Die vollständige Dokumentation ist integriert und jederzeit verfügbar.

## Screenshots

![Fenster Zugriffsschlüssel mit dem Berechtigungsbaum](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/01-access-keys.png)

In **Zugriffsschlüssel** erhält jeder Agent seinen eigenen Schlüssel. Wählen Sie eine **Persona** und legen Sie im **Berechtigungsbaum** fest, worauf er zugreifen darf, bis hin zu einer einzelnen Entität.

![Genehmigungsanfrage mit der Vorschau einer Dashboard-Karte](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/02-approvals.png)

In **Genehmigungen** sehen Sie eine Dashboard-Karte in der Vorschau, bevor sie hinzugefügt wird. Hier hat ein Agent darum gebeten, eine Karte hinzuzufügen, und nichts ändert sich, bis Sie **Genehmigen** wählen.

![Agent Chat geöffnet über einem Dashboard](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/03-agent-chat.png)

**Agent Chat** ist überall in Home Assistant verfügbar. Hier erstellt es eine Karte und wartet auf Ihre Genehmigung, bevor es etwas ändert.

![Mein Arbeitsbereich mit angedocktem Agent Chat](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/04-my-workspace.png)

**Mein Arbeitsbereich** enthält die Fenster, die Sie am häufigsten nutzen. Hier steht **Agent Chat** neben **Prüfprotokoll** und **Änderungen**.

## Sprachen

Das Bedienfeld unterstützt derzeit Englisch, Spanisch, Französisch, Italienisch, Deutsch, Niederländisch, Polnisch, Russisch, vereinfachtes Chinesisch, traditionelles Chinesisch, Koreanisch, Japanisch, Schwedisch, Dänisch, Norwegisch und Finnisch. Um die Sprache des Bedienfelds manuell zu ändern, öffnen Sie **Phoenix MCP > Einstellungen > Sprache** und wählen Sie eine Sprache aus. Wählen Sie **Auto**, um wieder der Sprache Ihres Home Assistant-Profils zu folgen.

## Dokumentation

Das Handbuch ist in das Bedienfeld integriert: Öffnen Sie das **Phoenix MCP**-Bedienfeld und wählen Sie im Menü **Hilfe** den Eintrag **Leitfaden**. Es erklärt jedes Fenster und jede Einstellung, was Agenten tun können, Berechtigungen, Fähigkeiten, MESA und Sicherheit. Es folgt Ihrer Auswahl, und Sie können es durchsuchen.

Zum ersten Mal hier? Öffnen Sie nach der unten beschriebenen Einrichtung das **Phoenix MCP**-Bedienfeld und wählen Sie **Agenten einrichten**. Ein Assistent führt Sie durch das Erstellen eines Zugriffsschlüssels, die Auswahl seiner Rechte und das Verbinden von Agent Chat oder Ihrer eigenen App.

## Voraussetzungen

- Home Assistant 2026.7.0 oder neuer.
- Eine Phoenix MCP-Instanz pro Home Assistant. Home Assistant installiert die Abhängigkeiten `Babel>=2.18.0` und `jsonschema>=4.26.0` für Sie.

## Installation über den Marketplace

Unter Home Assistant 2026.11 oder neuer:

1. Öffnen Sie **Einstellungen > Marketplace** und wählen Sie **Über Link hinzufügen**.
2. Geben Sie `https://github.com/leecaochang/Phoenix-MCP` ein und wählen Sie **Hinzufügen**. Beim ersten Mal bittet der Marketplace Sie, GitHub zu verbinden.
3. Öffnen Sie Phoenix MCP und wählen Sie **Installieren**. Starten Sie Home Assistant nur neu, wenn Sie dazu aufgefordert werden.

Beim Upgrade von HACS wechselt Phoenix MCP von selbst in den Marketplace. Verbinden Sie dort GitHub, um weiterhin Updates zu erhalten.

## Installation über HACS

Unter Home Assistant 2026.10 oder älter:

1. Öffnen Sie in HACS das Menü oben rechts und wählen Sie **Benutzerdefinierte Repositories**.
2. Geben Sie `https://github.com/leecaochang/Phoenix-MCP` ein, wählen Sie **Integration** als Kategorie und klicken Sie dann auf **Hinzufügen**.
3. Suchen Sie Phoenix MCP in der HACS-Integrationsliste, installieren Sie es und starten Sie Home Assistant neu.

Möchten Sie lieber manuell installieren? Kopieren Sie den Ordner `custom_components/phoenix_mcp` in das Home-Assistant-Konfigurationsverzeichnis unter `custom_components/phoenix_mcp` und starten Sie Home Assistant neu.

## Einrichtung

Öffnen Sie **Einstellungen > Geräte & Dienste > Integration hinzufügen** und suchen Sie nach **Phoenix MCP**. Schließen Sie die einstufige Einrichtung ab, öffnen Sie anschließend das **Phoenix MCP**-Bedienfeld in der Seitenleiste und wählen Sie **Agenten einrichten**.

## Probleme und Feedback

Melden Sie Probleme unter [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues).
