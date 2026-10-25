# Phoenix MCP

[![HACS](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/hassfest.yml)
[![Tests](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml/badge.svg)](https://github.com/leecaochang/Phoenix-MCP/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.7%2B-41BDF5?logo=home-assistant&logoColor=white)](https://www.home-assistant.io)


[English](README.md) | [Español](README.es.md) | [Français](README.fr.md) | [Italiano](README.it.md) | [Deutsch](README.de.md) | [Nederlands](README.nl.md) | [Polski](README.pl.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-Hant.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Русский](README.ru.md) | [Svenska](README.sv.md) | [Dansk](README.da.md) | [Norsk Bokmål](README.nb.md) | **Suomi**

Phoenix MCP antaa AI-agenteillesi rajatun, vähimmän oikeuden pääsyn Home Assistant -järjestelmään. Jokainen asiakas saa oman käyttöavaimensa, joka on rajattu täsmälleen sallimiisi entiteetteihin ja jolla on omat ominaisuudet, oma pyyntörajoitus ja valinnainen voimassaoloaika. Tarkastus on oletuksena käytössä ja määritettävissä tuloksittain, mikä tahansa käyttöavain voidaan peruuttaa välittömästi, ja entiteettikohtainen semanttinen turvakerros (MESA) voi tehdä laitteesta vain-vahvistettavan tai kokonaan kielletyn sen luonteen perusteella riippumatta siitä, mitä oikeuksia käyttöavaimelle on myönnetty. Neuvoa-antava on MESA:n oletustila: useimmat käytäntörajoitukset vain varoittavat, kunnes Pakotettu valitaan; vain luku -luonne ja nimenomaiset yksityisyyskiellot estävät silti.

Phoenix MCP toimii kokonaan Home Assistant -järjestelmän sisällä: erillistä käyttöön otettavaa palvelua ei tarvita, ydinpalvelimessa ei ole pilviriippuvuutta eikä käyttöönotto vaadi muuta kuin Phoenix MCP -paneelin (valinnaiset Agent Chat -, ääni- ja tekoälytehtäväominaisuudet lähettävät keskusteluja määrittämällesi mallitarjoajalle, ellet ohjaa niitä paikalliseen Ollama-palveluun). Se toimii niiden MCP-asiakkaiden kanssa, joita käytät jo (Claude Code, Cursor, ChatGPT/Codex, Gemini ja muut), tai voit jättää asiakkaan asentamatta ja keskustella suoraan Home Assistant -sovelluksen sisällä. Kummassakin tapauksessa opastettu käyttöönotto vie sinut uudesta käyttöavaimesta toimivaan agenttiin muutamassa minuutissa, ja tukena on 165 työkalun luettelo kokoonpanosi lukemiseen, ohjaamiseen ja luomiseen. Paneeli on saatavilla useilla kielillä, seuraa oletuksena Home Assistant -profiilisi kieltä ja palaa englantiin, kun kieltä ei ole saatavilla. Home Companion ja valinnainen päätösohjaus voivat myös lähettää pyyntöjä ja rajattuja tuloksia isännöidylle tarjoajalle. Ulkoiset MCP-asiakkaat noudattavat omia tarjoaja- ja tiedonjakamisasetuksiaan.

## Kielet

Paneeli tukee tällä hetkellä englantia, espanjaa, ranskaa, italiaa, saksaa, hollantia, puolaa, venäjää, yksinkertaistettua kiinaa, perinteistä kiinaa, koreaa, japania, ruotsia, tanskaa, norjaa ja suomea. Voit vaihtaa paneelin kielen manuaalisesti avaamalla **Phoenix MCP > Asetukset > Kieli** ja valitsemalla kielen. Valitse **Auto**, jos haluat jälleen seurata Home Assistant -profiilisi kieltä.

## Dokumentaatio

Täydellinen dokumentaatio, mukaan lukien työkaluluettelo, käyttöoikeudet, ominaisuudet, MESA ja ylläpito-API, on osoitteessa **[leecaochang.github.io/Phoenix-MCP](https://leecaochang.github.io/Phoenix-MCP/)**. Paneelin ohjelinkit osoittavat sinne.

Uusi täällä? Aloita **[Pika-aloitus](https://leecaochang.github.io/Phoenix-MCP/quickstart.html)**: se luo ensimmäisen käyttöavaimesi, myöntää sille yhden laitteen ja antaa sinun valita, keskusteletko Home Assistant -sovelluksen sisällä vai yhdistätkö ulkoisen agentin, kummassakin tapauksessa muutamassa minuutissa.

## Vaatimukset

- Home Assistant 2026.7.0 tai uudempi.
- Yksi Phoenix MCP -instanssi jokaista Home Assistant -asennusta kohden. Home Assistant asentaa Python-riippuvuudet `Babel>=2.18.0`, `jsonschema>=4.26.0` omien riippuvuusrajoitustensa mukaisesti; MESA sisältyy Phoenix MCP:hen.

## Asennus HACS:n kautta

1. Avaa HACS -sovelluksessa oikean yläkulman valikko ja valitse **Mukautetut tietovarastot**.
2. Anna `https://github.com/leecaochang/Phoenix-MCP` ja valitse kategoriaksi **Integraatio**, napsauta sitten **Lisää**.
3. Etsi Phoenix MCP HACS -sovelluksen integraatioluettelosta, asenna se ja käynnistä Home Assistant uudelleen.

Haluatko asentaa käsin? Kopioi `custom_components/phoenix_mcp`-kansio Home Assistant -konfiguraatiohakemistoosi `custom_components/phoenix_mcp`-polun alle ja käynnistä sitten uudelleen.

### Käyttöönotto

Siirry kohtaan **Asetukset > Laitteet ja palvelut > Lisää integraatio** ja etsi **Phoenix MCP**. Klikkaa läpi yksivaiheinen konfigurointivirta ja avaa sitten **Phoenix MCP** -paneeli sivupalkista. [Pika-aloitus](https://leecaochang.github.io/Phoenix-MCP/quickstart.html) jatkaa siitä.

## Ongelmat ja palaute

Ilmoita ongelmista osoitteessa [github.com/leecaochang/Phoenix-MCP/issues](https://github.com/leecaochang/Phoenix-MCP/issues).
