"""Shared fixtures for the Philips Smart Lock tests."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.philips_lock.cloud import const as cloud_const
from custom_components.philips_lock.cloud.realtime import RealtimeListener
from custom_components.philips_lock.const import (
    CONF_AREACODE,
    CONF_DATACENTER,
    CONF_EMAIL,
    CONF_ESN,
    CONF_PASSWORD,
    DOMAIN,
)

EMAIL = "user@example.com"
PASSWORD = "secret"
UID = "uid-1"
ESN = "LOCK000000001"
GATEWAY_ESN = "GW0000000001"
NA_CODE = "PhilipsNorthAmerica"
API = "https://api.example.com"
WS = "wss://ws.example.com"
LOGIN_URL = f"{cloud_const.AUTH_BASE}{cloud_const.LOGIN_PATH}"
DATACENTERS_URL = f"{cloud_const.AUTH_BASE}{cloud_const.DATACENTERS_PATH}"
DEVICE_LIST_URL = f"{API}{cloud_const.DEVICE_LIST_PATH}"
UNLOCK_URL = f"{API}{cloud_const.GATEWAY_OPEN_PATH}"
ENTITY_ID = "lock.front_door"

LOGIN_OK: dict[str, Any] = {
    "code": "200",
    "data": {"users": [{"uid": UID, "token": "token-na", "code": NA_CODE}]},
}
DATACENTERS_OK: dict[str, Any] = {
    "code": "200",
    "data": [{"code": NA_CODE, "apiAddr": f"{API}:443/", "wsAddr": WS}],
}
LOCK_RECORD: dict[str, Any] = {
    "deviceType": "LOCK",
    "wifiSN": ESN,
    "mac": "aabbccddeeff",
    "masterSn": GATEWAY_ESN,
    "userNumberId": 1,
    "lockNickname": "Front door",
    "dataCenter": "north-america",
    "productModel": "DDL610",
    "lockSoftwareVersion": "V1.43.002",
    "openStatus": 1,
}
GATEWAY_RECORD: dict[str, Any] = {"deviceType": "GATEWAY", "wifiSN": GATEWAY_ESN}


def device_list(open_status: int = 1) -> dict[str, Any]:
    """Return a device/list reply with the lock in the given state."""
    return {
        "code": "200",
        "data": {
            "wifiList": [{**LOCK_RECORD, "openStatus": open_status}, GATEWAY_RECORD]
        },
    }


class FakeListener:
    """Replace RealtimeListener; the test drives connect, status and disconnect."""

    def __init__(self) -> None:
        """Start without a running listener."""
        self.on_connect: Callable[[], Awaitable[None]] | None = None
        self.on_status: Callable[[bool], Awaitable[None]] | None = None
        self.on_disconnect: Callable[[], Awaitable[None]] | None = None
        self.error: Exception | None = None
        self.started = asyncio.Event()

    async def async_listen(self, on_connect, on_status, on_disconnect) -> None:
        """Record the callbacks and wait forever, or raise the prepared error."""
        self.on_connect, self.on_status, self.on_disconnect = (
            on_connect,
            on_status,
            on_disconnect,
        )
        self.started.set()
        if self.error is not None:
            raise self.error
        await asyncio.Event().wait()

    async def connect(self, hass: HomeAssistant) -> None:
        """Simulate a (re)connect and let the coordinator refresh."""
        await self.started.wait()
        assert self.on_connect is not None
        await self.on_connect()
        await hass.async_block_till_done()

    async def status(self, hass: HomeAssistant, is_locked: bool) -> None:
        """Simulate a pushed bolt state."""
        assert self.on_status is not None
        await self.on_status(is_locked)
        await hass.async_block_till_done()

    async def disconnect(self, hass: HomeAssistant) -> None:
        """Simulate a dropped socket."""
        assert self.on_disconnect is not None
        await self.on_disconnect()
        await hass.async_block_till_done()


@pytest.fixture(autouse=True)
async def auto_setup(hass: HomeAssistant, enable_custom_integrations: None) -> None:
    """Load custom_components/ and the core component the flows rely on."""
    assert await async_setup_component(hass, "homeassistant", {})


@pytest.fixture
def listener(monkeypatch: pytest.MonkeyPatch) -> FakeListener:
    """Replace the WebSocket listener of every coordinator."""
    fake = FakeListener()
    monkeypatch.setattr(
        RealtimeListener, "async_listen", lambda self, *cb: fake.async_listen(*cb)
    )
    return fake


@pytest.fixture
def cloud_ok(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    """Answer login, datacenters and device/list with a locked lock."""
    aioclient_mock.post(LOGIN_URL, json=LOGIN_OK)
    aioclient_mock.post(DATACENTERS_URL, json=DATACENTERS_OK)
    aioclient_mock.post(DEVICE_LIST_URL, json=device_list(1))
    return aioclient_mock


@pytest.fixture
def mock_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return a config entry for the lock, added but not set up."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Front door",
        unique_id=ESN,
        data={
            CONF_EMAIL: EMAIL,
            CONF_PASSWORD: PASSWORD,
            CONF_AREACODE: "84",
            CONF_ESN: ESN,
            CONF_DATACENTER: NA_CODE,
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def setup_entry(
    hass: HomeAssistant,
    mock_entry: MockConfigEntry,
    cloud_ok: AiohttpClientMocker,
    listener: FakeListener,
) -> MockConfigEntry:
    """Return the entry after a successful setup and a first WebSocket connect."""
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    await listener.connect(hass)
    return mock_entry
