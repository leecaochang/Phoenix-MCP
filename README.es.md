# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | **Español** | [Français](README.fr.md) | [Italiano](README.it.md) | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | [Polski](README.pl.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Русский](README.ru.md) | [Svenska](README.sv.md) | [Dansk](README.da.md) | [Norsk Bokmål](README.nb.md) | [Suomi](README.fi.md)

Phoenix MCP proporciona a sus agentes de IA un acceso seguro y limitado a Home Assistant mediante MCP (Model Context Protocol), un estándar abierto para conectar aplicaciones de IA con herramientas y datos. La seguridad es fundamental al usar IA, y Phoenix MCP ha pasado por muchos meses de desarrollo y revisiones de seguridad. Cada agente tiene su propia clave de acceso, limitada solo a las entidades y capacidades que usted permita. Una capa de seguridad adicional (MESA) puede exigir su confirmación para una entidad seleccionada o dejarla fuera de alcance, sean cuales sean los permisos concedidos a una clave de acceso.

Phoenix MCP se ejecuta por completo dentro de Home Assistant, sin ningún servicio aparte que desplegar. Use las aplicaciones que ya tiene, como Claude Code, Cursor, ChatGPT o Gemini, o chatee desde el propio Home Assistant con el proveedor de IA que elija, en la nube o localmente mediante Ollama.

Una configuración guiada le lleva de la instalación a un agente en funcionamiento en pocos minutos, respaldada por un catálogo de 174 herramientas para leer, controlar y crear su configuración. Phoenix MCP usa una interfaz de ventanas familiar y personalizable que facilita empezar. La documentación completa está integrada y disponible en todo momento.

## Capturas de pantalla

![Ventana Claves de acceso con el Árbol de permisos](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/01-access-keys.png)

En **Claves de acceso**, cada agente tiene su propia clave. Elija una **Persona** y use el **Árbol de permisos** para decidir a qué puede acceder, hasta una sola entidad.

![Solicitud de aprobación con la vista previa de una tarjeta del panel de control](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/02-approvals.png)

En **Aprobaciones** previsualiza una tarjeta del panel de control antes de que se añada. Aquí un agente pidió añadir una, y nada cambia hasta que usted elija **Aprobar**.

![Agent Chat abierto sobre un panel de control](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/03-agent-chat.png)

**Agent Chat** está disponible en todo Home Assistant. Aquí crea una tarjeta y espera su aprobación antes de cambiar nada.

![Mi espacio de trabajo con Agent Chat acoplado](https://raw.githubusercontent.com/leecaochang/Phoenix-MCP/main/screenshots/04-my-workspace.png)

**Mi espacio de trabajo** reúne las ventanas que más usa. Aquí **Agent Chat** aparece junto a **Registro de auditoría** y **Cambios**.

## Idiomas

El panel admite actualmente inglés, español, francés, italiano, alemán, neerlandés, polaco, ruso, chino simplificado, chino tradicional, coreano, japonés, sueco, danés, noruego y finés. Para cambiar el idioma manualmente, abra **Phoenix MCP > Configuración > Idioma** y elija un idioma. Seleccione **Automático** para volver a seguir el idioma del perfil de Home Assistant.

## Documentación

El manual está integrado en el panel: abra el panel **Phoenix MCP** y elija **Guía** en el menú **Ayuda**. Explica cada ventana y cada ajuste, lo que pueden hacer los agentes, los permisos, las capacidades, MESA y la seguridad. Sigue lo que usted selecciona, y puede buscar en él.

¿Es su primera vez? Después de la configuración que se indica más abajo, abra el panel **Phoenix MCP** y elija **Configurar un agente**. Un asistente le guía para crear una clave de acceso, decidir lo que puede hacer y conectar Agent Chat o su propia aplicación.

## Requisitos

- Home Assistant 2026.7.0 o posterior.
- Una instancia de Phoenix MCP por Home Assistant. Home Assistant instala por usted las dependencias `Babel>=2.18.0` y `jsonschema>=4.26.0`.

## Instalación desde Marketplace

En Home Assistant 2026.11 o posterior:

1. Vaya a **Configuración > Marketplace** y elija **Añadir desde enlace**.
2. Introduzca `https://github.com/leecaochang/Phoenix-MCP` y elija **Añadir**. La primera vez, Marketplace le pedirá que conecte GitHub.
3. Abra Phoenix MCP y elija **Instalar**. Reinicie Home Assistant solo si se lo pide.

Si actualiza desde HACS, Phoenix MCP pasa a Marketplace por sí solo. Conecte GitHub allí para seguir recibiendo actualizaciones.

## Instalación mediante HACS

En Home Assistant 2026.10 o anterior:

1. En HACS, abra el menú de la esquina superior derecha y elija **Repositorios personalizados**.
2. Introduzca `https://github.com/leecaochang/Phoenix-MCP`, seleccione **Integración** como categoría y haga clic en **Añadir**.
3. Encuentre Phoenix MCP en la lista de integraciones de HACS, instálelo y reinicie Home Assistant.

¿Prefiere instalar manualmente? Copie la carpeta `custom_components/phoenix_mcp` en el directorio de configuración de Home Assistant, en `custom_components/phoenix_mcp`, y reinicie.

## Configuración

Vaya a **Ajustes > Dispositivos y servicios > Añadir integración** y busque **Phoenix MCP**. Complete la configuración de un solo paso, abra el panel **Phoenix MCP** en la barra lateral y elija **Configurar un agente**.

## Incidencias y comentarios

Informe de las incidencias en [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues).
