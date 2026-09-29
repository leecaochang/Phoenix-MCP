"""Localized read rendering uses scoped values and conversation-local formatting."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.phoenix_mcp.command_display import async_display_state, display_number


@pytest.mark.parametrize('language,value,expected', [
    ('en-US', '1234.56789', '1234.56789'), ('de-DE', '1234.56789', '1234,56789'),
    ('fr-FR', 12.5, '12,5'), ('zh-Hans', 12.5, '12.5'), ('es', '0.00001', '0,00001'),
    ('bad_locale', 12.5, '12.5'), ('en', True, None), ('en', 'NaN', None),
    ('en', 'Infinity', None), ('en', '1e10000', None), ('en', {}, None),
])
def test_numbers_keep_precision_without_process_locale(language, value, expected):
    assert display_number(value, language) == expected


@pytest.mark.parametrize('language,expected', [('de', '21,375 °C'), ('en-US', '21.375 °C')])
async def test_sensor_value_and_unit_come_only_from_supplied_payload(hass, language, expected):
    hass.states.async_set('sensor.test', '999', {'unit_of_measurement': 'private unit'})
    payload = {'state': '21.375', 'attributes': {'unit_of_measurement': '°C'}}
    with patch('homeassistant.helpers.translation.async_get_translations', AsyncMock(return_value={})):
        assert await async_display_state(hass, 'sensor.test', payload, language) == expected


@pytest.mark.parametrize('domain,field,value,unit,expected', [
    ('light', 'brightness', 128, None, '50,2%'), ('media_player', 'volume_level', 0.375, None, '37,5%'),
    ('climate', 'temperature', 21.5, '°C', '21,5 °C'), ('weather', 'temperature', 70.25, '°F', '70,25 °F'),
    ('light', 'color_temp_kelvin', 3200, None, '3200 K'), ('sensor', 'battery_level', 55.125, None, '55,125'),
])
async def test_attribute_formats_preserve_native_units_and_scales(hass, domain, field, value, unit, expected):
    payload = {'attributes': {field: value}}
    assert await async_display_state(hass, domain+'.test', payload, 'de', field=field, temperature_unit=unit) == expected


@pytest.mark.parametrize('language,expected', [('en', 'Open'), ('de', 'Offen')])
async def test_device_class_translation_uses_conversation_language(hass, language, expected):
    entry = er.async_get(hass).async_get_or_create('binary_sensor', 'test', 'door')
    payload = {'state': 'on', 'attributes': {'device_class': 'door'}}
    hass.config.language = 'fr'
    translations = {'component.binary_sensor.entity_component.door.state.on': expected,
                    'component.binary_sensor.entity_component._.state.on': 'Wrong generic state'}
    with patch('homeassistant.helpers.translation.async_get_translations', AsyncMock(return_value=translations)) as load:
        assert await async_display_state(hass, entry.entity_id, payload, language) == expected
    load.assert_awaited_once_with(hass, language, 'entity_component', {'binary_sensor'})


async def test_platform_translation_precedes_domain_and_user_options_stay_literal(hass):
    entry = er.async_get(hass).async_get_or_create('sensor', 'test', 'mode', translation_key='mode')
    with patch('homeassistant.helpers.translation.async_get_translations', AsyncMock(return_value={
            'component.test.entity.sensor.mode.state.ready': 'Prêt'})) as load:
        assert await async_display_state(hass, entry.entity_id, {'state': 'ready'}, 'fr') == 'Prêt'
        load.assert_awaited_once_with(hass, 'fr', 'entity', {'test'})
        load.reset_mock()
        assert await async_display_state(hass, 'input_select.mode', {'state': 'Mode 2'}, 'fr') == 'Mode 2'
        load.assert_not_called()


@pytest.mark.parametrize('value', ['unknown', 'unavailable'])
async def test_special_states_do_not_claim_numeric_units(hass, value):
    with patch('homeassistant.helpers.translation.async_get_translations', AsyncMock()) as load:
        assert await async_display_state(hass, 'sensor.test', {'state': value, 'attributes': {'unit_of_measurement': '°C'}}, 'en') == value.title()
    load.assert_not_called()


async def test_missing_translations_fall_back_and_cancellation_propagates(hass):
    with patch('homeassistant.helpers.translation.async_get_translations', AsyncMock(side_effect=TimeoutError)):
        assert await async_display_state(hass, 'light.test', {'state': 'on'}, 'en') == 'on'
    with patch('homeassistant.helpers.translation.async_get_translations', AsyncMock(side_effect=asyncio.CancelledError)):
        with pytest.raises(asyncio.CancelledError):
            await async_display_state(hass, 'light.test', {'state': 'on'}, 'en')


async def test_installed_ha_translation_loader_supplies_device_class_words(hass):
    assert await async_display_state(hass, 'binary_sensor.door',
        {'state': 'on', 'attributes': {'device_class': 'door'}}, 'en') == 'Open'
