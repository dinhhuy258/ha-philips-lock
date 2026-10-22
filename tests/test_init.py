"""Tests for entry setup, the coordinator and reauth."""

import aiohttp
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.philips_lock.cloud.exceptions import AuthError
from custom_components.philips_lock.const import DOMAIN

from .conftest import (
    DATACENTERS_OK,
    DATACENTERS_URL,
    DEVICE_LIST_URL,
    ENTITY_ID,
    LOGIN_OK,
    LOGIN_URL,
    FakeListener,
    device_list,
)


async def test_setup_creates_a_locked_entity(
    hass: HomeAssistant, setup_entry: MockConfigEntry
) -> None:
    """Login, datacenters and device/list succeed; the lock entity is locked."""
    assert setup_entry.state is ConfigEntryState.LOADED
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == "locked"


async def test_entity_is_unavailable_until_the_socket_connects(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    cloud_ok: AiohttpClientMocker,
    listener: FakeListener,
) -> None:
    """Without a live socket the state is stale, so the entity is unavailable."""
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE
    await listener.connect(hass)
    assert hass.states.get(ENTITY_ID).state == "locked"


async def test_pushed_state_and_disconnect(
    hass: HomeAssistant, setup_entry: MockConfigEntry, listener: FakeListener
) -> None:
    """A bleAction state updates the entity; a drop makes it unavailable."""
    await listener.status(hass, False)
    assert hass.states.get(ENTITY_ID).state == "unlocked"
    await listener.disconnect(hass)
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE


async def test_reconnect_refreshes_from_device_list(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    cloud_ok: AiohttpClientMocker,
    listener: FakeListener,
) -> None:
    """After a reconnect the state is read again from the cloud cache."""
    await listener.disconnect(hass)
    cloud_ok.clear_requests()
    cloud_ok.post(DEVICE_LIST_URL, json=device_list(2))
    await listener.connect(hass)
    assert hass.states.get(ENTITY_ID).state == "unlocked"


async def test_reconnect_with_cloud_down_stays_unavailable(
    hass: HomeAssistant,
    setup_entry: MockConfigEntry,
    cloud_ok: AiohttpClientMocker,
    listener: FakeListener,
) -> None:
    """A failed refresh after a reconnect keeps the entity unavailable."""
    await listener.disconnect(hass)
    cloud_ok.clear_requests()
    cloud_ok.post(DEVICE_LIST_URL, exc=aiohttp.ClientError("boom"))
    await listener.connect(hass)
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE


async def test_setup_with_wrong_password_starts_reauth(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A refused login at setup puts the entry in the reauth state."""
    aioclient_mock.post(LOGIN_URL, json={"code": "1001", "msg": "Wrong password"})
    assert not await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_REAUTH


async def test_setup_with_cloud_down_retries(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A network failure at setup is retried later."""
    aioclient_mock.post(LOGIN_URL, exc=aiohttp.ClientError("boom"))
    assert not await hass.config_entries.async_setup(mock_entry.entry_id)
    assert mock_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_without_the_lock_retries(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """A device/list without the configured lock is retried later."""
    aioclient_mock.post(LOGIN_URL, json=LOGIN_OK)
    aioclient_mock.post(DATACENTERS_URL, json=DATACENTERS_OK)
    aioclient_mock.post(DEVICE_LIST_URL, json={"code": "200", "data": {"wifiList": []}})
    assert not await hass.config_entries.async_setup(mock_entry.entry_id)
    assert mock_entry.state is ConfigEntryState.SETUP_RETRY


async def test_listener_auth_error_starts_reauth_once(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    cloud_ok: AiohttpClientMocker,
    listener: FakeListener,
) -> None:
    """A re-login refused during the session makes the entity unavailable and starts reauth."""
    listener.error = AuthError("Login refused")
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == SOURCE_REAUTH


async def test_unload(hass: HomeAssistant, setup_entry: MockConfigEntry) -> None:
    """Unloading leaves only the restored, unavailable state of the registered entity."""
    assert await hass.config_entries.async_unload(setup_entry.entry_id)
    await hass.async_block_till_done()
    assert setup_entry.state is ConfigEntryState.NOT_LOADED
    assert hass.states.get(ENTITY_ID).state == STATE_UNAVAILABLE
