# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | **Español** | [Français](README.fr.md) | [Italiano](README.it.md) | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | [Polski](README.pl.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Русский](README.ru.md) | [Svenska](README.sv.md) | [Dansk](README.da.md) | [Norsk Bokmål](README.nb.md) | [Suomi](README.fi.md)

Phoenix MCP proporciona a sus agentes de IA un acceso a Home Assistant con ámbito limitado y privilegios mínimos. Cada cliente recibe su propia clave de acceso, limitada exactamente a las entidades que usted permita, con sus propias capacidades, límite de velocidad y expiración opcional. La auditoría está habilitada por defecto y es configurable por resultado; cualquier clave de acceso puede revocarse al instante, y la capa de seguridad semántica por entidad (MESA) puede hacer que un dispositivo sea solo de confirmación o esté fuera de los límites por su propia naturaleza, sin importar los permisos que se concedan a una clave de acceso. El modo predeterminado de MESA es Consultivo: la mayoría de las restricciones de política solo advierten hasta seleccionar Obligatorio; la naturaleza de solo lectura y las denegaciones explícitas de privacidad siguen bloqueando.

Phoenix MCP se ejecuta completamente dentro de Home Assistant, sin ningún servicio aparte que desplegar, sin dependencia de la nube en el servidor principal y sin más configuración que el panel de Phoenix MCP (las funciones opcionales Chat de agente, voz y tarea de IA envían las conversaciones al proveedor de modelos que configure, a menos que las dirija a un Ollama local). Funciona con los clientes MCP que ya utiliza (Claude Code, Cursor, ChatGPT/Codex, Gemini y otros), o puede omitir la instalación de un cliente y chatear desde el propio Home Assistant. En cualquier caso, una configuración guiada le lleva de una nueva clave de acceso a un agente funcional en minutos, respaldada por un catálogo de 165 herramientas para leer, controlar y crear su configuración. El panel está disponible en varios idiomas, sigue de forma predeterminada el idioma del perfil de Home Assistant y usa el inglés como alternativa cuando ese idioma no está disponible. Home Companion y el enrutamiento de decisiones opcional también pueden enviar solicitudes y resultados autorizados a un proveedor alojado. Los clientes MCP externos siguen sus propios ajustes de proveedor y uso compartido de datos.

## Idiomas

El panel admite actualmente inglés, español, francés, italiano, alemán, neerlandés, polaco, ruso, chino simplificado, chino tradicional, coreano, japonés, sueco, danés, noruego y finés. Para cambiar el idioma manualmente, abra **Phoenix MCP > Configuración > Idioma** y elija un idioma. Seleccione **Automático** para volver a seguir el idioma del perfil de Home Assistant.

## Documentación

La documentación completa, incluida la referencia de herramientas, los permisos, las capacidades, MESA y la API de administración, está en **[leecaochang.github.io/Phoenix-MCP](https://leecaochang.github.io/Phoenix-MCP/)**. Los enlaces de ayuda del panel apuntan allí.

¿Es nuevo? Empiece por el **[Inicio rápido](https://leecaochang.github.io/Phoenix-MCP/quickstart.html)**: crea su primera clave de acceso, le concede un dispositivo y luego le deja elegir entre chatear desde Home Assistant o conectar un agente externo; en ambos casos, en pocos minutos.

## Requisitos

- Home Assistant 2026.7.0 o posterior.
- Una instancia de Phoenix MCP por Home Assistant. Home Assistant instala las dependencias de Python `Babel>=2.18.0`, `jsonschema>=4.26.0` según sus restricciones de dependencias; MESA viene incluido con Phoenix MCP.

## Instalación mediante HACS

1. En HACS, abra el menú de la esquina superior derecha y elija **Repositorios personalizados**.
2. Introduzca `https://github.com/leecaochang/Phoenix-MCP`, seleccione **Integración** como categoría y haga clic en **Añadir**.
3. Encuentre Phoenix MCP en la lista de integraciones de HACS, instálelo y reinicie Home Assistant.

¿Prefiere instalar manualmente? Copie la carpeta `custom_components/phoenix_mcp` en el directorio de configuración de Home Assistant, en `custom_components/phoenix_mcp`, y reinicie.

### Configuración

Vaya a **Ajustes > Dispositivos y servicios > Añadir integración** y busque **Phoenix MCP**. Complete el flujo de configuración de un solo paso y abra el panel **Phoenix MCP** en la barra lateral. A partir de ahí, siga el [Inicio rápido](https://leecaochang.github.io/Phoenix-MCP/quickstart.html).

## Incidencias y comentarios

Informe de las incidencias en [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues).
