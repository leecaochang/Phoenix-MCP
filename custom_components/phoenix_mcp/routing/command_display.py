"""Localize only scoped read results; translation never fetches entity state."""
from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from babel import Locale, UnknownLocaleError
from babel.numbers import format_decimal
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er, translation
from homeassistant.core import HomeAssistant

from .routing_tables import (
    COMMAND_ROUTING_NUMERIC_READ_DOMAINS, COMMAND_ROUTING_TRANSLATION_TIMEOUT_SECONDS,
    COMMAND_ROUTING_READ_PERCENT_SCALES, COMMAND_ROUTING_SHARED_SERVICES,
)
from ..entity_commands import attribute_display
from ..locales import canonical_language
from ..catalog_text import voice_text


def display_number(value: Any, language: str) -> str | None:
    """Use CLDR separators without rounding, grouping or a process-global locale."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite() or abs(number.adjusted()) > 100:
            return None
        try:
            locale = Locale.parse(language.replace('_', '-'), sep='-')
        except (ValueError, UnknownLocaleError):
            locale = Locale.parse(canonical_language(language), sep='-')
        with localcontext() as context:
            context.prec = max(28, len(number.as_tuple().digits) + 8)
            return format_decimal(number, locale=locale, decimal_quantization=False, group_separator=False)
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _display_value(value: Any, attributes: dict, domain: str, field: str | None,
                   language: str, temperature_unit: str | None) -> str | None:
    """Formatting may load CLDR data, so callers run this outside the event loop."""

    if field is not None:
        raw = attribute_display(field, value, temperature_unit, domain)
        if raw is None:
            return None
        scale = COMMAND_ROUTING_READ_PERCENT_SCALES.get(field)
        if scale is not None and domain in scale[0]:
            number = display_number(round(value * 100 / scale[1], 1), language)
            return number + '%' if number is not None else None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            number = display_number(value, language)
            if number is not None:
                suffix = raw[len(f'{value:g}'):] if raw.startswith(f'{value:g}') else ''
                return number + suffix
        return raw
    if not isinstance(value, str) or len(value) > 1024:
        return None
    if value == 'unknown':
        return voice_text(language, 'local_state_unknown')
    if value == 'unavailable':
        return voice_text(language, 'local_state_unavailable')
    number = display_number(value, language) if domain in COMMAND_ROUTING_NUMERIC_READ_DOMAINS else None
    unit = attributes.get('unit_of_measurement')
    return (number if number is not None else value) + (' ' + unit if isinstance(unit, str) and unit else '')


async def async_state_translations(hass: HomeAssistant, language: object) -> dict[str, str]:
    """HA's translated entity states for every loaded domain, or {} when unavailable.

    Also loads the language's CLDR data in an executor, so a caller on the event
    loop can then use display_number and reading_text without file I/O.
    """
    resolved = canonical_language(language)
    await hass.async_add_executor_job(display_number, 0, resolved)
    try:
        async with asyncio.timeout(COMMAND_ROUTING_TRANSLATION_TIMEOUT_SECONDS):
            return await translation.async_get_translations(hass, resolved, 'entity_component')
    except (TimeoutError, HomeAssistantError, ValueError):
        return {}


def _field_text(translated: dict[str, str], domain: str, service: str, key: str, value: object) -> str | None:
    """One choice-valued service data field as HA words it ("Muted: No"), or None.

    Numbers are left out: the reply's state check already covers them, and HA has no unit for them here.
    """
    attribute = f'component.{domain}.entity_component._.state_attributes.{key}'
    name = translated.get(f'{attribute}.name') or translated.get(f'component.{domain}.services.{service}.fields.{key}.name')
    if isinstance(value, bool):
        states: tuple[str, ...] = ('true', 'on') if value else ('false', 'off')
    elif isinstance(value, str):
        states = (value,)
    else:
        return None
    shown = next((label for state in states for label in (
        translated.get(f'{attribute}.state.{state}'), translated.get(f'component.{domain}.selector.{key}.options.{state}'))
        if isinstance(label, str) and label), None)
    if shown is None and isinstance(value, str):
        shown = value
    return f'{name}: {shown}' if isinstance(name, str) and name and shown is not None else None


async def async_service_label(hass: HomeAssistant, domain: str, service: str, language: object,
                              data: dict | None = None) -> str | None:
    """HA's translated name for one service, or None when HA has none or cannot load it.

    A service several categories share (COMMAND_ROUTING_SHARED_SERVICES) is followed by its data
    fields as HA words them, so a mute and an unmute do not sound the same.
    """
    resolved = canonical_language(language)
    shared = (domain, service) in COMMAND_ROUTING_SHARED_SERVICES and isinstance(data, dict) and bool(data)
    try:
        async with asyncio.timeout(COMMAND_ROUTING_TRANSLATION_TIMEOUT_SECONDS):
            translated = await translation.async_get_translations(hass, resolved, 'services', {domain})
            if shared:
                for category in ('entity_component', 'selector'):
                    translated = {**translated, **await translation.async_get_translations(hass, resolved, category, {domain})}
    except (TimeoutError, HomeAssistantError, ValueError):
        return None
    label = translated.get(f'component.{domain}.services.{service}.name')
    if not isinstance(label, str) or not label or len(label) > 1024:
        return None
    details = [text for key, value in (data or {}).items()
               if shared and (text := _field_text(translated, domain, service, key, value))]
    text = f"{label} ({', '.join(details)})" if details else label
    return text if len(text) <= 1024 else label


def state_label(translations: dict[str, str], domain: str, value: str, device_class: object = None) -> str | None:
    """HA's label for one entity state, preferring the device-class variant."""
    keys = ([f'component.{domain}.entity_component.{device_class}.state.{value}'] if isinstance(device_class, str) else [])
    for key in [*keys, f'component.{domain}.entity_component._.state.{value}']:
        label = translations.get(key)
        if isinstance(label, str) and label and len(label) <= 1024:
            return label
    return None


def reading_text(value: str, attributes: dict, domain: str, language: str, translations: dict[str, str]) -> str:
    """One state as spoken: HA's translated label, a CLDR number with its unit, or the raw value.

    Needs the language's CLDR data already loaded (async_state_translations).
    """
    if value not in ('unknown', 'unavailable') and domain not in ('select', 'input_select', 'text', 'input_text'):
        label = state_label(translations, domain, value, attributes.get('device_class'))
        if label is not None:
            return label
    return _display_value(value, attributes, domain, None, language, None) or value


async def async_display_state(hass: HomeAssistant, entity_id: str, payload: dict, language: object,
                              *, field: str | None = None, temperature_unit: str | None = None) -> str | None:
    """Render supplied scrubbed values using registry identity and HA translations."""
    language = language if isinstance(language, str) else canonical_language(language)
    attributes = payload.get('attributes', {})
    if not isinstance(attributes, dict):
        return None
    value = payload.get('state') if field is None else attributes.get(field)
    domain = entity_id.split('.', 1)[0]
    display = await hass.async_add_executor_job(_display_value, value, attributes, domain, field, language, temperature_unit)
    if display is None or not isinstance(value, str) or value in ('unknown', 'unavailable'):
        return display
    # Exact option/text values are user data, not translatable state enums.
    if domain in ('select', 'input_select', 'text', 'input_text'):
        return display
    entry = er.async_get(hass).async_get(entity_id)
    component = f'component.{domain}.entity_component'
    suffix = f'state_attributes.{field}.state.{value}' if field is not None else f'state.{value}'
    device_class = attributes.get('device_class')
    keys = ([f'{component}.{device_class}.{suffix}'] if isinstance(device_class, str) else []) + [f'{component}._.{suffix}']
    try:
        async with asyncio.timeout(COMMAND_ROUTING_TRANSLATION_TIMEOUT_SECONDS):
            if entry is not None and entry.translation_key:
                translated = await translation.async_get_translations(hass, canonical_language(language), 'entity', {entry.platform})
                key = f'component.{entry.platform}.entity.{domain}.{entry.translation_key}.{suffix}'
                if isinstance(translated.get(key), str) and translated[key] and len(translated[key]) <= 1024:
                    return translated[key]
            translated = await translation.async_get_translations(hass, canonical_language(language), 'entity_component', {domain})
            for key in keys:
                if isinstance(translated.get(key), str) and translated[key] and len(translated[key]) <= 1024:
                    return translated[key]
    except (TimeoutError, HomeAssistantError, ValueError):
        # Missing translations cannot invalidate an already-scoped read.
        pass
    return display
