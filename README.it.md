# Phoenix MCP

[![HACS](https://github.com/leecaochang/phoenix-mcp/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/phoenix-mcp/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/phoenix-mcp/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/phoenix-mcp/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/phoenix-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/phoenix-mcp/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2025.2%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | [Français](README.fr.md) | **Italiano** | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | [Polski](README.pl.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Русский](README.ru.md)

Phoenix MCP offre ai tuoi agenti IA un accesso a Home Assistant con ambito limitato e privilegi minimi. Ogni client riceve un proprio token, limitato esattamente alle entità che autorizzi, con capacità, limite di frequenza e scadenza opzionale propri. Il controllo è attivo per impostazione predefinita ed è configurabile per esito, qualsiasi token può essere revocato all'istante e il livello di sicurezza semantica per entità (MESA) può rendere un dispositivo a sola conferma o vietato in base alla sua natura, indipendentemente dalle autorizzazioni concesse al token.

Phoenix MCP funziona interamente dentro Home Assistant, senza processi aggiuntivi, senza dipendenze cloud nel server principale e senza alcuna configurazione oltre al pannello di Phoenix MCP (le funzioni opzionali Agent Chat, voce e AI Task inviano le conversazioni al fornitore del modello che configuri, a meno che tu non le indirizzi a un Ollama locale). Funziona con i client MCP che già usi (Claude Code, Cursor, ChatGPT/Codex, Gemini e altri), oppure puoi evitare del tutto di installare un client e chattare direttamente dentro Home Assistant. In entrambi i casi una configurazione guidata ti porta da un nuovo token a un agente funzionante in pochi minuti, con il supporto di un catalogo di 164 strumenti per leggere, controllare e scrivere la tua configurazione. Il pannello è disponibile in più lingue, segue per impostazione predefinita la lingua del tuo profilo di Home Assistant e ripiega sull'inglese quando quella lingua non è disponibile.

## Lingue

Attualmente il pannello supporta inglese, spagnolo, francese, italiano, tedesco, olandese, polacco, russo, cinese semplificato, cinese tradizionale, coreano e giapponese. Per cambiare manualmente la lingua del pannello, apri **Phoenix MCP > Impostazioni > Lingua** e scegli una lingua. Scegli **Auto** per seguire di nuovo la lingua del tuo profilo di Home Assistant.

## Documentazione

La documentazione completa, con il riferimento degli strumenti, le autorizzazioni, le capacità, MESA e l'API di amministrazione, è su **[leecaochang.github.io/phoenix-mcp](https://leecaochang.github.io/phoenix-mcp/)**. I link di aiuto del pannello portano lì.

Nuovo da queste parti? Inizia dalla **[Guida rapida](https://leecaochang.github.io/phoenix-mcp/quickstart.html)**: crea il tuo primo token, gli concede un dispositivo e poi ti lascia scegliere se chattare da dentro Home Assistant o collegare un agente esterno, in pochi minuti in entrambi i casi.

## Requisiti

- Home Assistant 2025.2.0 o versioni successive.
- Un'istanza di Phoenix MCP per Home Assistant. Home Assistant installa la dipendenza Python con versione fissata `voluptuous-serialize==2.7.0`; MESA è incluso in Phoenix MCP.

## Installazione tramite HACS

1. In HACS apri **Integrazioni**, poi il menu in alto a destra, e scegli **Repository personalizzati**.
2. Inserisci `https://github.com/leecaochang/phoenix-mcp` e seleziona **Integrazione** come categoria, poi fai clic su **Aggiungi**.
3. Trova Phoenix MCP nell'elenco delle integrazioni di HACS, installalo e riavvia Home Assistant.

Preferisci installare a mano? Copia la cartella `custom_components/phoenix_mcp` nella cartella di configurazione di Home Assistant sotto `custom_components/phoenix_mcp`, poi riavvia.

### Configurazione

Vai su **Impostazioni > Dispositivi e servizi > Aggiungi integrazione** e cerca **Phoenix MCP**. Completa il flusso di configurazione a un solo passaggio, poi apri il pannello **Phoenix MCP** nella barra laterale. La [Guida rapida](https://leecaochang.github.io/phoenix-mcp/quickstart.html) prosegue da lì.

## Problemi e commenti

Segnala i problemi su [github.com/leecaochang/phoenix-mcp/issues](https://github.com/leecaochang/phoenix-mcp/issues).
