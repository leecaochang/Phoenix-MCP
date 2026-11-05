# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | **Français** | [Italiano](README.it.md) | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | [Polski](README.pl.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Русский](README.ru.md) | [Svenska](README.sv.md) | [Dansk](README.da.md) | [Norsk Bokmål](README.nb.md) | [Suomi](README.fi.md)

Phoenix MCP offre à vos agents IA un accès à Home Assistant limité par périmètre et selon le principe du moindre privilège. Chaque client reçoit sa propre clé d’accès, limitée exactement aux entités que vous autorisez, avec ses propres capacités, limite de débit et date d'expiration facultative. L'audit est activé par défaut et configurable par résultat, toute clé d’accès peut être révoquée instantanément, et la couche de sécurité sémantique par entité (MESA) peut imposer une confirmation ou interdire l'accès à un appareil selon sa nature, quels que soient les droits accordés à la clé d’accès. Le mode MESA par défaut est Consultatif : la plupart des restrictions de politique avertissent seulement tant que le mode Appliqué n’est pas sélectionné ; la nature en lecture seule et les refus explicites de confidentialité restent bloquants.

Phoenix MCP s'exécute entièrement dans Home Assistant, sans service distinct à déployer, sans dépendance au cloud pour le serveur principal et sans configuration au-delà du panneau Phoenix MCP. Les fonctions facultatives Agent Chat, vocales et Tâche d'IA envoient les conversations au fournisseur de modèle que vous configurez, sauf si vous les dirigez vers un Ollama local. Il fonctionne avec les clients MCP que vous utilisez déjà, notamment Claude Code, Cursor, ChatGPT/Codex et Gemini. Vous pouvez aussi ne pas installer de client et discuter directement dans Home Assistant. Dans les deux cas, une configuration guidée vous fait passer d’une nouvelle clé d’accès à un agent opérationnel en quelques minutes, avec un catalogue de 174 outils pour lire, contrôler et créer votre configuration. Le panneau est disponible dans plusieurs langues, suit par défaut la langue de votre profil Home Assistant et utilise l'anglais si cette langue n'est pas disponible. Home Companion et le routage décisionnel facultatif peuvent aussi envoyer des requêtes et des résultats autorisés à un fournisseur hébergé. Les clients MCP externes suivent leurs propres réglages de fournisseur et de partage des données.

## Langues

Le panneau prend actuellement en charge l'anglais, l'espagnol, le français, l'italien, l'allemand, le néerlandais, le polonais, le russe, le chinois simplifié, le chinois traditionnel, le coréen, le japonais, le suédois, le danois, le norvégien et le finnois. Pour changer manuellement la langue du panneau, ouvrez **Phoenix MCP > Paramètres > Langue** et choisissez une langue. Choisissez **Auto** pour suivre à nouveau la langue de votre profil Home Assistant.

## Documentation

Le manuel est intégré au panneau : ouvrez le panneau **Phoenix MCP** et choisissez **Guide** dans le menu **Aide**. Il explique chaque fenêtre et chaque paramètre, ce que les agents peuvent faire, les autorisations, les capacités, MESA, la sécurité et l'API d'administration, et vous pouvez y effectuer des recherches.

Vous débutez ? Ouvrez le panneau **Phoenix MCP** et choisissez **Configurer un agent** dans Clés d’accès. Il crée votre première clé d’accès, définit ce qu'elle peut faire, puis vous permet de discuter dans Home Assistant ou de connecter une application externe, et se termine par un test que vous pouvez suivre.

## Prérequis

- Home Assistant 2026.7.0 ou version ultérieure.
- Une instance Phoenix MCP par Home Assistant. Home Assistant installe les dépendances Python `Babel>=2.18.0`, `jsonschema>=4.26.0` selon ses contraintes de dépendances ; MESA est inclus avec Phoenix MCP.

## Installation depuis le Marketplace

Sur Home Assistant 2026.11 ou version ultérieure :

1. Accédez à **Paramètres > Marketplace** et choisissez **Ajouter grâce à un lien**.
2. Saisissez `https://github.com/leecaochang/Phoenix-MCP` et choisissez **Ajouter**. La première fois, le Marketplace vous demande de vous connecter à GitHub.
3. Ouvrez Phoenix MCP, choisissez **Installer** et redémarrez Home Assistant.

Si vous effectuez la mise à niveau depuis HACS, Phoenix MCP passe tout seul au Marketplace. Connectez-y GitHub pour continuer à recevoir les mises à jour.

## Installation avec HACS

Sur Home Assistant 2026.10 ou version antérieure :

1. Dans HACS, ouvrez le menu en haut à droite et choisissez **Dépôts personnalisés**.
2. Saisissez `https://github.com/leecaochang/Phoenix-MCP`, sélectionnez **Intégration** comme catégorie, puis cliquez sur **Ajouter**.
3. Trouvez Phoenix MCP dans la liste des intégrations HACS, installez-le, puis redémarrez Home Assistant.

Vous préférez une installation manuelle ? Copiez le dossier `custom_components/phoenix_mcp` dans le répertoire de configuration de Home Assistant, sous `custom_components/phoenix_mcp`, puis redémarrez.

### Configuration

Ouvrez **Paramètres > Appareils et services > Ajouter une intégration** et recherchez **Phoenix MCP**. Terminez le flux de configuration en une étape, puis ouvrez le panneau **Phoenix MCP** dans la barre latérale et choisissez **Configurer un agent**.

## Problèmes et retours

Signalez les problèmes sur [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues).
