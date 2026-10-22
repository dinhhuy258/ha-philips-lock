"""Tests for the cloud client."""

from collections.abc import AsyncIterator
import json

import aiohttp
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.philips_lock.cloud import const
from custom_components.philips_lock.cloud.client import (
    Datacenter,
    LockRecord,
    PhilipsCloud,
    datacenter_code,
    normalize_mac,
)
from custom_components.philips_lock.cloud.exceptions import (
    AuthError,
    CloudConnectionError,
    CommandError,
)

LOGIN_URL = f"{const.AUTH_BASE}{const.LOGIN_PATH}"
DATACENTERS_URL = f"{const.AUTH_BASE}{const.DATACENTERS_PATH}"
API = "https://api.example.com"
DEVICE_LIST_URL = f"{API}{const.DEVICE_LIST_PATH}"
UNLOCK_URL = f"{API}{const.GATEWAY_OPEN_PATH}"
NA = Datacenter(
    code="PhilipsNorthAmerica", api_base=API, ws_addr="wss://ws.example.com"
)

LOGIN_OK = {
    "code": "200",
    "data": {
        "users": [
            {"uid": "uid-1", "token": "token-na", "code": "PhilipsNorthAmerica"},
            {"uid": "uid-1", "token": "token-sg", "code": "PhilipsSingapore"},
        ]
    },
}
LOCK_RECORD = {
    "deviceType": "LOCK",
    "wifiSN": "LOCK000000001",
    "mac": "aabbccddeeff",
    "masterSn": "GW0000000001",
    "userNumberId": 1,
    "lockNickname": "Front door",
    "dataCenter": "north-america",
    "productModel": "DDL610",
    "lockSoftwareVersion": "V1.43.002",
    "openStatus": 1,
}
GATEWAY_RECORD = {"deviceType": "GATEWAY", "wifiSN": "GW0000000001"}
DEVICE_LIST_OK = {"code": "200", "data": {"wifiList": [LOCK_RECORD, GATEWAY_RECORD]}}
NOT_LOGGED_IN = {"code": "444", "msg": "Not logged in"}


@pytest.fixture
async def session(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> AsyncIterator[aiohttp.ClientSession]:
    """Return an aiohttp session answered by aioclient_mock."""
    session = aioclient_mock.create_session(hass.loop)
    yield session
    await session.close()


@pytest.fixture
def client(session: aiohttp.ClientSession) -> PhilipsCloud:
    """Return a client for the test account."""
    return PhilipsCloud(session, "user@example.com", "secret", "84")


def _body(call: tuple) -> dict:
    return json.loads(call[2]) if isinstance(call[2], str | bytes) else call[2]


def test_normalize_mac() -> None:
    """Any separator style becomes AA:BB:CC:DD:EE:FF."""
    assert normalize_mac("aabbccddeeff") == "AA:BB:CC:DD:EE:FF"
    assert normalize_mac("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"
    assert normalize_mac("AA:BB:CC:DD:EE:FF") == "AA:BB:CC:DD:EE:FF"


def test_datacenter_code() -> None:
    """A record's dataCenter maps to the login reply's code."""
    assert datacenter_code("north-america") == "PhilipsNorthAmerica"
    assert datacenter_code("southeast-asia") == "PhilipsSingapore"
    assert datacenter_code("singapore") == "PhilipsSingapore"


def test_lock_record_from_device_list() -> None:
    """LockRecord reads the fields the integration uses."""
    lock = LockRecord.from_record(LOCK_RECORD)
    assert lock.esn == "LOCK000000001"
    assert lock.mac == "AA:BB:CC:DD:EE:FF"
    assert lock.gateway_esn == "GW0000000001"
    assert lock.user_number_id == 1
    assert lock.name == "Front door"
    assert lock.datacenter == "PhilipsNorthAmerica"
    assert lock.model == "DDL610"
    assert lock.sw_version == "V1.43.002"
    assert lock.is_locked is True
    assert LockRecord.from_record({**LOCK_RECORD, "openStatus": 2}).is_locked is False
    assert LockRecord.from_record({**LOCK_RECORD, "openStatus": 0}).is_locked is None


async def test_login_stores_uid_and_tokens(
    client: PhilipsCloud, aioclient_mock: AiohttpClientMocker
) -> None:
    """A code 200 reply yields the uid and one token per datacenter."""
    aioclient_mock.post(LOGIN_URL, json=LOGIN_OK)
    await client.async_login()
    assert client.uid == "uid-1"
    assert client.token_for("PhilipsNorthAmerica") == "token-na"
    assert client.datacenter_codes() == ["PhilipsNorthAmerica", "PhilipsSingapore"]
    method, _url, _data, headers = aioclient_mock.mock_calls[0]
    assert method == "POST"
    assert _body(aioclient_mock.mock_calls[0]) == {
        "identifier": "user@example.com",
        "credential": "secret",
        "areacode": "84",
    }
    assert headers["User-Agent"] == const.LOGIN_USER_AGENT
    assert headers["reqSource"] == "app"


async def test_login_rejected_raises_auth_error(
    client: PhilipsCloud, aioclient_mock: AiohttpClientMocker
) -> None:
    """Any non-200 code is an authentication failure."""
    aioclient_mock.post(LOGIN_URL, json={"code": "1001", "msg": "Wrong password"})
    with pytest.raises(AuthError, match="1001"):
        await client.async_login()


async def test_login_network_failure_raises_connection_error(
    client: PhilipsCloud, aioclient_mock: AiohttpClientMocker
) -> None:
    """A transport error or a non-JSON reply is a connection error."""
    aioclient_mock.post(LOGIN_URL, exc=aiohttp.ClientError("boom"))
    with pytest.raises(CloudConnectionError):
        await client.async_login()
    aioclient_mock.clear_requests()
    aioclient_mock.post(LOGIN_URL, status=502, text="<html>Bad gateway</html>")
    with pytest.raises(CloudConnectionError):
        await client.async_login()


async def test_fetch_datacenters_strips_the_port(
    client: PhilipsCloud, aioclient_mock: AiohttpClientMocker
) -> None:
    """apiAddr arrives as https://host:443/ and is stored as https://host."""
    aioclient_mock.post(
        DATACENTERS_URL,
        json={
            "code": "200",
            "data": [
                {
                    "code": "PhilipsNorthAmerica",
                    "apiAddr": "https://api.example.com:443/",
                    "wsAddr": "wss://ws.example.com",
                },
                {
                    "code": "PhilipsSingapore",
                    "apiAddr": "https://sg.example.com",
                    "wsAddr": "",
                },
            ],
        },
    )
    datacenters = await client.async_fetch_datacenters()
    assert datacenters["PhilipsNorthAmerica"] == NA
    assert datacenters["PhilipsSingapore"].api_base == "https://sg.example.com"
    assert datacenters["PhilipsSingapore"].ws_addr == ""


async def test_device_list_returns_lock_records(
    client: PhilipsCloud, aioclient_mock: AiohttpClientMocker
) -> None:
    """device/list is sent with the datacenter token and returns the locks only."""
    aioclient_mock.post(LOGIN_URL, json=LOGIN_OK)
    aioclient_mock.post(DEVICE_LIST_URL, json=DEVICE_LIST_OK)
    await client.async_login()
    locks = await client.async_device_list(NA)
    assert [lock.esn for lock in locks] == ["LOCK000000001"]
    _method, _url, _data, headers = aioclient_mock.mock_calls[1]
    assert _body(aioclient_mock.mock_calls[1]) == {"uid": "uid-1"}
    assert headers["token"] == "token-na"
    assert headers["k-tenant"] == "philips"


async def test_rejected_token_relogins_once_and_retries(
    client: PhilipsCloud, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 444 reply triggers one re-login and the request is sent again."""

    async def device_list_reply(method, url, data):
        return _device_list_after_relogin(aioclient_mock)

    aioclient_mock.post(LOGIN_URL, json=LOGIN_OK)
    aioclient_mock.post(DEVICE_LIST_URL, side_effect=device_list_reply)
    await client.async_login()
    locks = await client.async_device_list(NA, user_initiated=True)
    assert locks[0].esn == "LOCK000000001"
    posts = [url.path for _, url, _, _ in aioclient_mock.mock_calls]
    assert posts == [
        const.LOGIN_PATH,
        const.DEVICE_LIST_PATH,
        const.LOGIN_PATH,
        const.DEVICE_LIST_PATH,
    ]


def _device_list_after_relogin(aioclient_mock: AiohttpClientMocker):
    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMockResponse,
    )

    logins = sum(
        1 for _, url, _, _ in aioclient_mock.mock_calls if url.path == const.LOGIN_PATH
    )
    payload = DEVICE_LIST_OK if logins >= 2 else NOT_LOGGED_IN
    return AiohttpClientMockResponse("POST", DEVICE_LIST_URL, json=payload)


async def test_rejected_token_twice_raises_auth_error(
    client: PhilipsCloud, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 444 after a fresh login means the session cannot be kept."""
    aioclient_mock.post(LOGIN_URL, json=LOGIN_OK)
    aioclient_mock.post(DEVICE_LIST_URL, json=NOT_LOGGED_IN)
    await client.async_login()
    with pytest.raises(AuthError):
        await client.async_device_list(NA, user_initiated=True)


async def test_background_relogin_is_rate_limited(
    client: PhilipsCloud, aioclient_mock: AiohttpClientMocker
) -> None:
    """A second background re-login inside the window is refused as transient."""
    aioclient_mock.post(LOGIN_URL, json=LOGIN_OK)
    await client.async_login()
    with pytest.raises(CloudConnectionError, match="rate-limited"):
        await client.async_relogin("token-na")
    assert len(aioclient_mock.mock_calls) == 1
    await client.async_relogin("token-na", user_initiated=True)
    assert len(aioclient_mock.mock_calls) == 2


async def test_relogin_skipped_when_token_already_replaced(
    client: PhilipsCloud, aioclient_mock: AiohttpClientMocker
) -> None:
    """A caller holding a token that is no longer ours does not log in again."""
    aioclient_mock.post(LOGIN_URL, json=LOGIN_OK)
    await client.async_login()
    await client.async_relogin("stale-token", user_initiated=True)
    assert len(aioclient_mock.mock_calls) == 1


async def test_unlock_sends_an_encrypted_gateway_command(
    client: PhilipsCloud, aioclient_mock: AiohttpClientMocker
) -> None:
    """The unlock body is {"encryptData": ...} with the encrypt_data header."""
    aioclient_mock.post(LOGIN_URL, json=LOGIN_OK)
    aioclient_mock.post(UNLOCK_URL, json={"code": 200, "msg": "Success"})
    await client.async_login()
    await client.async_unlock(NA, LockRecord.from_record(LOCK_RECORD))
    _method, _url, _data, headers = aioclient_mock.mock_calls[1]
    assert set(_body(aioclient_mock.mock_calls[1])) == {"encryptData"}
    assert headers[const.ENCRYPT_DATA_HEADER] == const.ENCRYPT_DATA_HEADER
    assert headers["token"] == "token-na"


async def test_unlock_refused_raises_command_error(
    client: PhilipsCloud, aioclient_mock: AiohttpClientMocker
) -> None:
    """Any code other than 200 is a refusal; a non-JSON reply is a connection error."""
    aioclient_mock.post(LOGIN_URL, json=LOGIN_OK)
    aioclient_mock.post(UNLOCK_URL, json={"code": 500, "msg": "Gateway offline"})
    await client.async_login()
    with pytest.raises(CommandError, match="Gateway offline"):
        await client.async_unlock(NA, LockRecord.from_record(LOCK_RECORD))
    aioclient_mock.clear_requests()
    aioclient_mock.post(UNLOCK_URL, status=404, text="<html>Resource not found</html>")
    with pytest.raises(CloudConnectionError):
        await client.async_unlock(NA, LockRecord.from_record(LOCK_RECORD))
