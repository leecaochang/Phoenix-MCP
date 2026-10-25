# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | [Français](README.fr.md) | **Italiano** | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | [Polski](README.pl.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Русский](README.ru.md) | [Svenska](README.sv.md) | [Dansk](README.da.md) | [Norsk Bokmål](README.nb.md) | [Suomi](README.fi.md)

Phoenix MCP offre ai tuoi agenti IA un accesso a Home Assistant con ambito limitato e privilegi minimi. Ogni client riceve una propria chiave di accesso, limitata esattamente alle entità che autorizzi, con capacità, limite di frequenza e scadenza opzionale propri. La registrazione di audit è attiva per impostazione predefinita ed è configurabile per esito, qualsiasi chiave di accesso può essere revocata all'istante e il livello di sicurezza semantica per entità (MESA) può rendere un dispositivo a sola conferma o vietato in base alla sua natura, indipendentemente dalle autorizzazioni concesse alla chiave di accesso. La modalità MESA predefinita è Consultiva: la maggior parte delle restrizioni genera solo avvisi finché non si seleziona Applicata; la natura di sola lettura e i divieti espliciti di privacy continuano a bloccare.

Phoenix MCP funziona interamente dentro Home Assistant, senza servizi separati da distribuire, senza dipendenze cloud nel server principale e senza alcuna configurazione oltre al pannello di Phoenix MCP (le funzioni opzionali Agent Chat, voce e AI Task inviano le conversazioni al fornitore del modello che configuri, a meno che tu non le indirizzi a un Ollama locale). Funziona con i client MCP che già usi (Claude Code, Cursor, ChatGPT/Codex, Gemini e altri), oppure puoi evitare del tutto di installare un client e chattare direttamente dentro Home Assistant. In entrambi i casi una configurazione guidata ti porta da una nuova chiave di accesso a un agente funzionante in pochi minuti, con il supporto di un catalogo di 165 strumenti per leggere, controllare e scrivere la tua configurazione. Il pannello è disponibile in più lingue, segue per impostazione predefinita la lingua del tuo profilo di Home Assistant e ripiega sull'inglese quando quella lingua non è disponibile. Anche Home Companion e l’instradamento decisionale facoltativo possono inviare richieste e risultati autorizzati a un fornitore ospitato. I client MCP esterni seguono le proprie impostazioni del fornitore e di condivisione dei dati.

## Lingue

Attualmente il pannello supporta inglese, spagnolo, francese, italiano, tedesco, olandese, polacco, russo, cinese semplificato, cinese tradizionale, coreano, giapponese, svedese, danese, norvegese e finlandese. Per cambiare manualmente la lingua del pannello, apri **Phoenix MCP > Impostazioni > Lingua** e scegli una lingua. Scegli **Auto** per seguire di nuovo la lingua del tuo profilo di Home Assistant.

## Documentazione

La documentazione completa, con il riferimento degli strumenti, le autorizzazioni, le capacità, MESA e l'API di amministrazione, è su **[leecaochang.github.io/Phoenix-MCP](https://leecaochang.github.io/Phoenix-MCP/)**. I link di aiuto del pannello portano lì.

Nuovo da queste parti? Inizia dalla **[Guida rapida](https://leecaochang.github.io/Phoenix-MCP/quickstart.html)**: crea la tua prima chiave di accesso, le concede accesso a un dispositivo e poi ti lascia scegliere se chattare da dentro Home Assistant o collegare un agente esterno, in pochi minuti in entrambi i casi.

## Requisiti

- Home Assistant 2026.7.0 o versioni successive.
- Un’istanza di Phoenix MCP per Home Assistant. Home Assistant installa le dipendenze Python `Babel>=2.18.0`, `jsonschema>=4.26.0` nel rispetto dei propri vincoli sulle dipendenze; MESA è incluso in Phoenix MCP.

## Installazione tramite HACS

1. In HACS, apri il menu in alto a destra e scegli **Repository personalizzati**.
2. Inserisci `https://github.com/leecaochang/Phoenix-MCP` e seleziona **Integrazione** come categoria, poi fai clic su **Aggiungi**.
3. Trova Phoenix MCP nell'elenco delle integrazioni di HACS, installalo e riavvia Home Assistant.

Preferisci installare a mano? Copia la cartella `custom_components/phoenix_mcp` nella cartella di configurazione di Home Assistant sotto `custom_components/phoenix_mcp`, poi riavvia.

### Configurazione

Vai su **Impostazioni > Dispositivi e servizi > Aggiungi integrazione** e cerca **Phoenix MCP**. Completa il flusso di configurazione a un solo passaggio, poi apri il pannello **Phoenix MCP** nella barra laterale. La [Guida rapida](https://leecaochang.github.io/Phoenix-MCP/quickstart.html) prosegue da lì.

## Problemi e commenti

Segnala i problemi su [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues).
