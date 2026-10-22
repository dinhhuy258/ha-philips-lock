"""Tests for the config flow."""

from typing import Any

import aiohttp
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.philips_lock.const import (
    CONF_AREACODE,
    CONF_DATACENTER,
    CONF_EMAIL,
    CONF_ESN,
    CONF_PASSWORD,
    DOMAIN,
)

from .conftest import (
    DATACENTERS_URL,
    DEVICE_LIST_URL,
    EMAIL,
    ESN,
    LOCK_RECORD,
    LOGIN_OK,
    LOGIN_URL,
    NA_CODE,
    PASSWORD,
    FakeListener,
)

USER_INPUT = {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD, CONF_AREACODE: "84"}
SG_API = "https://sg.example.com"
LOGIN_TWO: dict[str, Any] = {
    "code": "200",
    "data": {
        "users": [
            {"uid": "uid-1", "token": "token-na", "code": NA_CODE},
            {"uid": "uid-1", "token": "token-sg", "code": "PhilipsSingapore"},
        ]
    },
}
DATACENTERS_TWO: dict[str, Any] = {
    "code": "200",
    "data": [
        {
            "code": NA_CODE,
            "apiAddr": "https://api.example.com:443/",
            "wsAddr": "wss://ws.example.com",
        },
        {"code": "PhilipsSingapore", "apiAddr": SG_API, "wsAddr": ""},
    ],
}


async def _start(hass: HomeAssistant) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def test_one_lock_creates_the_entry(
    hass: HomeAssistant, cloud_ok: AiohttpClientMocker, listener: FakeListener
) -> None:
    """With one lock the entry is created without a pick step."""
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Front door"
    assert result["data"] == {**USER_INPUT, CONF_ESN: ESN, CONF_DATACENTER: NA_CODE}
    assert result["result"].unique_id == ESN
    assert result["result"].state is ConfigEntryState.LOADED


async def test_two_locks_show_a_pick_step(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, listener: FakeListener
) -> None:
    """Locks from every datacenter are offered; a failing datacenter is skipped."""
    aioclient_mock.post(LOGIN_URL, json=LOGIN_TWO)
    aioclient_mock.post(DATACENTERS_URL, json=DATACENTERS_TWO)
    other = {**LOCK_RECORD, "wifiSN": "LOCK000000002", "lockNickname": "Back door"}
    aioclient_mock.post(
        DEVICE_LIST_URL,
        json={"code": "200", "data": {"wifiList": [LOCK_RECORD, other]}},
    )
    aioclient_mock.post(f"{SG_API}/homeaccess/device/list", status=500, text="error")
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pick"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ESN: "LOCK000000002"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Back door"
    assert result["data"][CONF_ESN] == "LOCK000000002"
    assert result["data"][CONF_DATACENTER] == NA_CODE


async def test_lock_in_unknown_datacenter_is_skipped(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A lock homed in a datacenter without a token cannot be used."""
    aioclient_mock.post(LOGIN_URL, json=LOGIN_OK)
    aioclient_mock.post(DATACENTERS_URL, json=DATACENTERS_TWO)
    aioclient_mock.post(
        DEVICE_LIST_URL,
        json={
            "code": "200",
            "data": {"wifiList": [{**LOCK_RECORD, "dataCenter": "europe"}]},
        },
    )
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_locks"}


async def test_wrong_password(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A refused login shows invalid_auth."""
    aioclient_mock.post(LOGIN_URL, json={"code": "1001", "msg": "Wrong password"})
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_cloud_down(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A network failure shows cannot_connect."""
    aioclient_mock.post(LOGIN_URL, exc=aiohttp.ClientError("boom"))
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_same_lock_twice_aborts(
    hass: HomeAssistant, setup_entry: MockConfigEntry, cloud_ok: AiohttpClientMocker
) -> None:
    """A lock that is already configured is not added again."""
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_the_password(
    hass: HomeAssistant, setup_entry: MockConfigEntry, cloud_ok: AiohttpClientMocker
) -> None:
    """The reauth step stores the new password and reloads the entry."""
    result = await setup_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-secret"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert setup_entry.data[CONF_PASSWORD] == "new-secret"
    assert setup_entry.state is ConfigEntryState.LOADED


async def test_reauth_with_wrong_password(
    hass: HomeAssistant, setup_entry: MockConfigEntry, cloud_ok: AiohttpClientMocker
) -> None:
    """A refused password keeps the reauth form open."""
    cloud_ok.clear_requests()
    cloud_ok.post(LOGIN_URL, json={"code": "1001", "msg": "Wrong password"})
    result = await setup_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
