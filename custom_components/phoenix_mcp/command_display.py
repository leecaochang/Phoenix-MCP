"""Localize only scoped read results; translation never fetches entity state."""
from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from babel import Locale, UnknownLocaleError
from babel.numbers import format_decimal
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er, translation

from .const import COMMAND_ROUTING_NUMERIC_READ_DOMAINS, COMMAND_ROUTING_TRANSLATION_TIMEOUT_SECONDS
from .helpers import canonical_language, voice_text


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
    from .const import COMMAND_ROUTING_READ_PERCENT_SCALES
    from .entity_commands import attribute_display

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


async def async_display_state(hass: Any, entity_id: str, payload: dict, language: object,
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
