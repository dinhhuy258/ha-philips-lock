"""Tests for the lock entity."""

import aiohttp
from homeassistant.components.lock import (
    DOMAIN as LOCK_DOMAIN,
    SERVICE_LOCK,
    SERVICE_UNLOCK,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.philips_lock.const import DOMAIN

from .conftest import ENTITY_ID, ESN, UNLOCK_URL, FakeListener


async def test_entity_and_device(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """The entity carries the lock's unique id and a device with model and version."""
    entity = er.async_get(hass).async_get(ENTITY_ID)
    assert entity is not None
    assert entity.unique_id == ESN
    device = dr.async_get(hass).async_get(entity.device_id)
    assert device.identifiers == {(DOMAIN, ESN)}
    assert device.manufacturer == "Philips"
    assert device.model == "DDL610"
    assert device.sw_version == "V1.43.002"
    assert device.serial_number == ESN
    state = hass.states.get(ENTITY_ID)
    assert state.attributes["friendly_name"] == "Front door"
    assert state.attributes["supported_features"] == 0


async def test_unlock_sends_the_gateway_command(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    cloud_ok: AiohttpClientMocker,
    listener: FakeListener,
) -> None:
    """lock.unlock posts the encrypted command; the state follows the push, not the call."""
    cloud_ok.post(UNLOCK_URL, json={"code": 200, "msg": "Success"})
    await hass.services.async_call(
        LOCK_DOMAIN, SERVICE_UNLOCK, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )
    _method, url, _data, headers = cloud_ok.mock_calls[-1]
    assert url.path == "/v3/gateway/set-lock-open"
    assert headers["encrypt_data"] == "encrypt_data"
    assert hass.states.get(ENTITY_ID).state == "locked"
    await listener.status(hass, False)
    assert hass.states.get(ENTITY_ID).state == "unlocked"


async def test_unlock_refused_raises(
    hass: HomeAssistant, setup_entry: MockConfigEntry, cloud_ok: AiohttpClientMocker
) -> None:
    """A refusal or a network failure surfaces as a Home Assistant error."""
    cloud_ok.post(UNLOCK_URL, json={"code": 500, "msg": "Gateway offline"})
    with pytest.raises(HomeAssistantError, match="Gateway offline"):
        await hass.services.async_call(
            LOCK_DOMAIN, SERVICE_UNLOCK, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
        )
    cloud_ok.clear_requests()
    cloud_ok.post(UNLOCK_URL, exc=aiohttp.ClientError("boom"))
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            LOCK_DOMAIN, SERVICE_UNLOCK, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
        )


async def test_lock_is_not_supported(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """lock.lock explains that the lock relocks itself."""
    with pytest.raises(ServiceValidationError, match="locks itself"):
        await hass.services.async_call(
            LOCK_DOMAIN, SERVICE_LOCK, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
        )
