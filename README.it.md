# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | [Français](README.fr.md) | **Italiano** | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | [Polski](README.pl.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Русский](README.ru.md) | [Svenska](README.sv.md) | [Dansk](README.da.md) | [Norsk Bokmål](README.nb.md) | [Suomi](README.fi.md)

Phoenix MCP offre ai tuoi agenti IA un accesso sicuro e limitato a Home Assistant tramite MCP (Model Context Protocol), uno standard aperto per collegare le app di IA a strumenti e dati. La sicurezza è fondamentale quando si usa l'IA, e Phoenix MCP ha alle spalle molti mesi di sviluppo e revisioni di sicurezza. Ogni agente ha la propria chiave di accesso, limitata alle sole entità e capacità che autorizzi. Un ulteriore livello di sicurezza (MESA) può richiedere la tua conferma per un'entità selezionata o renderla off-limits, indipendentemente dalle autorizzazioni concesse a una chiave di accesso.

Phoenix MCP funziona interamente all'interno di Home Assistant, senza servizi separati da distribuire. Usa le app che hai già, come Claude Code, Cursor, ChatGPT o Gemini, oppure chatta direttamente da Home Assistant con il fornitore di IA che preferisci, nel cloud o in locale tramite Ollama.

Una configurazione guidata ti porta dall'installazione a un agente funzionante in pochi minuti, con un catalogo di 174 strumenti per leggere, controllare e creare la tua configurazione. Phoenix MCP usa un'interfaccia a finestre familiare e personalizzabile, che rende facile iniziare. La documentazione completa è integrata e sempre disponibile.

## Schermate

![Finestra Chiavi di accesso con l'Albero delle autorizzazioni](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/01-access-keys.png)

In **Chiavi di accesso**, ogni agente ha la propria chiave. Scegli una **Persona**, poi usa l'**Albero delle autorizzazioni** per decidere a cosa può accedere, fino a una singola entità.

![Richiesta di approvazione con l'anteprima di una scheda della plancia](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/02-approvals.png)

In **Approvazioni** vedi l'anteprima di una scheda della plancia prima che venga aggiunta. Qui un agente ha chiesto di aggiungerne una, e nulla cambia finché non scegli **Approva**.

![Agent Chat aperto sopra una plancia](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/03-agent-chat.png)

**Agent Chat** è disponibile ovunque in Home Assistant. Qui crea una scheda, poi attende la tua approvazione prima di modificare qualcosa.

![Mia area di lavoro con Agent Chat ancorato](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/04-my-workspace.png)

**Mia area di lavoro** raccoglie le finestre che usi di più. Qui **Agent Chat** è accanto a **Registro di controllo** e **Modifiche**.

## Lingue

Attualmente il pannello supporta inglese, spagnolo, francese, italiano, tedesco, olandese, polacco, russo, cinese semplificato, cinese tradizionale, coreano, giapponese, svedese, danese, norvegese e finlandese. Per cambiare manualmente la lingua del pannello, apri **Phoenix MCP > Impostazioni > Lingua** e scegli una lingua. Scegli **Auto** per seguire di nuovo la lingua del tuo profilo di Home Assistant.

## Documentazione

Il manuale è integrato nel pannello: apri il pannello **Phoenix MCP** e scegli **Guida** dal menu **Aiuto**. Spiega ogni finestra e impostazione, cosa possono fare gli agenti, le autorizzazioni, le capacità, MESA e la sicurezza. Segue ciò che selezioni, e puoi effettuare ricerche al suo interno.

Prima volta qui? Dopo la configurazione descritta più avanti, apri il pannello **Phoenix MCP** e scegli **Configura un agente**. Una procedura guidata ti accompagna nella creazione di una chiave di accesso, nella scelta di cosa può fare e nel collegamento di Agent Chat o della tua app.

## Requisiti

- Home Assistant 2026.7.0 o versioni successive.
- Un’istanza di Phoenix MCP per Home Assistant. Home Assistant installa per te le dipendenze `Babel>=2.18.0` e `jsonschema>=4.26.0`.

## Installazione dal Marketplace

Su Home Assistant 2026.11 o versioni successive:

1. Vai in **Impostazioni > Marketplace** e scegli **Aggiungi dal collegamento**.
2. Inserisci `https://github.com/leecaochang/Phoenix-MCP` e scegli **Aggiungi**. La prima volta il Marketplace ti chiede di collegare GitHub.
3. Apri Phoenix MCP e scegli **Installa**. Riavvia Home Assistant solo se te lo chiede.

Se aggiorni da HACS, Phoenix MCP passa da solo al Marketplace. Collega GitHub lì per continuare a ricevere gli aggiornamenti.

## Installazione tramite HACS

Su Home Assistant 2026.10 o versioni precedenti:

1. In HACS, apri il menu in alto a destra e scegli **Repository personalizzati**.
2. Inserisci `https://github.com/leecaochang/Phoenix-MCP` e seleziona **Integrazione** come categoria, poi fai clic su **Aggiungi**.
3. Trova Phoenix MCP nell'elenco delle integrazioni di HACS, installalo e riavvia Home Assistant.

Preferisci installare a mano? Copia la cartella `custom_components/phoenix_mcp` nella cartella di configurazione di Home Assistant sotto `custom_components/phoenix_mcp`, poi riavvia.

## Configurazione

Vai su **Impostazioni > Dispositivi e servizi > Aggiungi integrazione** e cerca **Phoenix MCP**. Completa la configurazione a un solo passaggio, poi apri il pannello **Phoenix MCP** nella barra laterale e scegli **Configura un agente**.

## Problemi e commenti

Segnala i problemi su [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues).
