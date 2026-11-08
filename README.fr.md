# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | **Français** | [Italiano](README.it.md) | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | [Polski](README.pl.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Русский](README.ru.md) | [Svenska](README.sv.md) | [Dansk](README.da.md) | [Norsk Bokmål](README.nb.md) | [Suomi](README.fi.md)

Phoenix MCP offre à vos agents IA un accès sûr et limité à Home Assistant grâce à MCP (Model Context Protocol), un standard ouvert qui relie les applications d’IA aux outils et aux données. La sécurité est essentielle lorsqu’on utilise l’IA, et Phoenix MCP a bénéficié de nombreux mois de développement et de revues de sécurité. Chaque agent possède sa propre clé d’accès, limitée aux seules entités et capacités que vous autorisez. Une couche de sécurité supplémentaire ([MESA](https://github.com/leecaochang/mesa-core)) peut exiger votre confirmation pour une entité choisie ou la rendre interdite, quelles que soient les autorisations accordées à une clé d’accès.

Phoenix MCP s’exécute entièrement dans Home Assistant, sans service séparé à déployer. Utilisez les applications que vous avez déjà, comme Claude Code, Cursor, ChatGPT ou Gemini, ou discutez directement dans Home Assistant avec le fournisseur d’IA de votre choix, dans le cloud ou localement via Ollama.

Une configuration guidée vous mène de l’installation à un agent opérationnel en quelques minutes, avec un catalogue de 174 outils pour lire, contrôler et rédiger votre configuration. Phoenix MCP utilise une interface à fenêtres familière et personnalisable qui facilite la prise en main. La documentation complète est intégrée et disponible à tout moment.

## Captures d’écran

![Fenêtre Clés d’accès avec l’Arbre des autorisations](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/01-access-keys.png)

Dans **Clés d’accès**, chaque agent possède sa propre clé. Choisissez une **Personnalité**, puis utilisez l’**Arbre des autorisations** pour décider ce qu’il peut atteindre, jusqu’à une seule entité.

![Demande d’approbation avec l’aperçu d’une carte de tableau de bord](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/02-approvals.png)

Dans **Approbations**, vous prévisualisez une carte de tableau de bord avant son ajout. Ici, un agent a demandé d’en ajouter une, et rien ne change tant que vous n’avez pas choisi **Approuver**.

![Agent Chat ouvert par-dessus un tableau de bord](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/03-agent-chat.png)

**Agent Chat** est disponible partout dans Home Assistant. Ici, il crée une carte, puis attend votre approbation avant de modifier quoi que ce soit.

![Mon espace de travail avec Agent Chat ancré](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/04-my-workspace.png)

**Mon espace de travail** réunit les fenêtres que vous utilisez le plus. Ici, **Agent Chat** se trouve à côté du **Journal d’audit** et des **Modifications**.

## Langues

Le panneau prend actuellement en charge l'anglais, l'espagnol, le français, l'italien, l'allemand, le néerlandais, le polonais, le russe, le chinois simplifié, le chinois traditionnel, le coréen, le japonais, le suédois, le danois, le norvégien et le finnois. Pour changer manuellement la langue du panneau, ouvrez **Phoenix MCP > Paramètres > Langue** et choisissez une langue. Choisissez **Auto** pour suivre à nouveau la langue de votre profil Home Assistant.

## Documentation

Le manuel est intégré au panneau : ouvrez le panneau **Phoenix MCP** et choisissez **Guide** dans le menu **Aide**. Il explique chaque fenêtre et chaque paramètre, ce que les agents peuvent faire, les autorisations, les capacités, MESA et la sécurité. Il suit ce que vous sélectionnez, et vous pouvez y effectuer des recherches.

Vous débutez ? Après la configuration décrite plus bas, ouvrez le panneau **Phoenix MCP** et choisissez **Configurer un agent**. Un assistant vous guide pour créer une clé d’accès, choisir ce qu’elle peut faire et connecter Agent Chat ou votre propre application.

## Prérequis

- Home Assistant 2026.7.0 ou version ultérieure.
- Une instance Phoenix MCP par Home Assistant. Home Assistant installe pour vous les dépendances `Babel>=2.18.0` et `jsonschema>=4.26.0`.

## Installation depuis le Marketplace

Sur Home Assistant 2026.11 ou version ultérieure :

1. Accédez à **Paramètres > Marketplace** et choisissez **Ajouter grâce à un lien**.
2. Saisissez `https://github.com/leecaochang/Phoenix-MCP` et choisissez **Ajouter**. La première fois, le Marketplace vous demande de vous connecter à GitHub.
3. Ouvrez Phoenix MCP et choisissez **Installer**. Redémarrez Home Assistant uniquement s’il vous le demande.

Si vous effectuez la mise à niveau depuis HACS, Phoenix MCP passe tout seul au Marketplace. Connectez-y GitHub pour continuer à recevoir les mises à jour.

## Installation avec HACS

Sur Home Assistant 2026.10 ou version antérieure :

1. Dans HACS, ouvrez le menu en haut à droite et choisissez **Dépôts personnalisés**.
2. Saisissez `https://github.com/leecaochang/Phoenix-MCP`, sélectionnez **Intégration** comme catégorie, puis cliquez sur **Ajouter**.
3. Trouvez Phoenix MCP dans la liste des intégrations HACS, installez-le, puis redémarrez Home Assistant.

Vous préférez une installation manuelle ? Copiez le dossier `custom_components/phoenix_mcp` dans le répertoire de configuration de Home Assistant, sous `custom_components/phoenix_mcp`, puis redémarrez.

## Configuration

Ouvrez **Paramètres > Appareils et services > Ajouter une intégration** et recherchez **Phoenix MCP**. Terminez la configuration en une étape, puis ouvrez le panneau **Phoenix MCP** dans la barre latérale et choisissez **Configurer un agent**.

## Problèmes et retours

Signalez les problèmes sur [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues).
