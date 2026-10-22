"""HTTP client of the Philips Home Access cloud for one account."""

import asyncio
from dataclasses import dataclass
import logging
import time
from typing import Any

import aiohttp

from . import const, crypto
from .exceptions import AuthError, CloudConnectionError, CommandError

_LOGGER = logging.getLogger(__name__)


def normalize_mac(mac: str) -> str:
    """Return a MAC as AA:BB:CC:DD:EE:FF, the form the gateway command expects."""
    digits = mac.replace(":", "").replace("-", "").replace(" ", "").upper()
    if len(digits) != 12:
        return digits
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


def datacenter_code(data_center: str) -> str:
    """Map a record's dataCenter ("north-america") to a login code ("PhilipsNorthAmerica")."""
    if data_center in const.DATACENTER_ALIASES:
        return const.DATACENTER_ALIASES[data_center]
    parts = data_center.replace("_", "-").split("-")
    return "Philips" + "".join(part.capitalize() for part in parts)


def _strip_port(addr: str) -> str:
    """Return https://host for the https://host:443/ form the cloud sends."""
    addr = (addr or "").strip().rstrip("/")
    if addr.startswith("https://") and addr.endswith(":443"):
        return addr[:-4]
    return addr


@dataclass(frozen=True)
class Datacenter:
    """Hosts of one datacenter, from POST /datacenters."""

    code: str
    api_base: str
    ws_addr: str


@dataclass(frozen=True)
class LockRecord:
    """The fields of a device/list lock record that the integration uses."""

    esn: str
    mac: str
    gateway_esn: str
    user_number_id: int
    name: str
    datacenter: str
    model: str
    sw_version: str
    is_locked: bool | None

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> LockRecord:
        """Build a LockRecord from one wifiList entry."""
        open_status = record.get("openStatus")
        is_locked: bool | None = None
        if open_status == const.OPEN_STATUS_LOCKED:
            is_locked = True
        elif open_status == const.OPEN_STATUS_UNLOCKED:
            is_locked = False
        return cls(
            esn=record.get("wifiSN", ""),
            mac=normalize_mac(record.get("mac", "")),
            gateway_esn=record.get("masterSn", ""),
            user_number_id=int(record.get("userNumberId") or 0),
            name=record.get("lockNickname") or record.get("wifiSN", ""),
            datacenter=datacenter_code(record.get("dataCenter", "")),
            model=record.get("productModel", ""),
            sw_version=record.get("lockSoftwareVersion", ""),
            is_locked=is_locked,
        )


class PhilipsCloud:
    """One Philips Home Access account: login, datacenters, devices, unlock."""

    def __init__(
        self, session: aiohttp.ClientSession, email: str, password: str, areacode: str
    ) -> None:
        """Keep the credentials; nothing is sent until async_login."""
        self._session = session
        self._email = email.strip()
        self._password = password
        self._areacode = areacode
        self.uid = ""
        self._tokens: dict[str, str] = {}
        # One login at a time: concurrent 444s each asking for a login would,
        # with one session per account, kill each other's fresh tokens.
        self._login_lock = asyncio.Lock()
        self._last_login: float | None = None

    def token_for(self, code: str) -> str:
        """Return the token of a datacenter, or an empty string."""
        return self._tokens.get(code, "")

    def datacenter_codes(self) -> list[str]:
        """Return the datacenters the login handed a token for."""
        return list(self._tokens)

    @staticmethod
    def _auth_headers() -> dict[str, str]:
        return {
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": const.LOGIN_USER_AGENT,
            "lang": const.LANGUAGE,
            "language": const.LANGUAGE,
            "reqSource": "app",
            "timestamp": str(int(time.time())),
        }

    async def _post_json(
        self, url: str, body: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        """POST JSON and return the JSON object reply; anything else is a connection error."""
        try:
            async with self._session.post(url, json=body, headers=headers) as resp:
                status = resp.status
                try:
                    data = await resp.json(content_type=None)
                except ValueError:
                    data = None
        except aiohttp.ClientError as err:
            raise CloudConnectionError(f"POST {url} failed: {err}") from err
        if not isinstance(data, dict):
            raise CloudConnectionError(
                f"POST {url} -> HTTP {status}, not a JSON object"
            )
        return data

    async def async_login(self) -> None:
        """Log in and keep the uid and the token of every datacenter."""
        async with self._login_lock:
            await self._login()

    async def _login(self) -> None:
        self._last_login = time.monotonic()
        data = await self._post_json(
            const.AUTH_BASE + const.LOGIN_PATH,
            {
                "identifier": self._email,
                "credential": self._password,
                "areacode": self._areacode,
            },
            self._auth_headers(),
        )
        code = str(data.get("code"))
        if code != "200":
            raise AuthError(f"Login refused: {data.get('msg')} (code {code})")
        users = (data.get("data") or {}).get("users") or []
        if not users:
            raise AuthError("Login returned no session")
        self.uid = users[0].get("uid", "")
        self._tokens = {user["code"]: user["token"] for user in users}
        _LOGGER.debug("Logged in; datacenters %s", list(self._tokens))

    async def async_relogin(
        self, rejected_token: str, *, user_initiated: bool = False
    ) -> None:
        """Renew a rejected token.

        Skipped when another caller already replaced it. Background callers are
        rate-limited and get a CloudConnectionError inside the window; a command
        a person is waiting on bypasses the limit.
        """
        async with self._login_lock:
            if rejected_token not in self._tokens.values():
                return
            if (
                not user_initiated
                and self._last_login is not None
                and time.monotonic() - self._last_login < const.RELOGIN_MIN_INTERVAL
            ):
                raise CloudConnectionError("re-login rate-limited")
            _LOGGER.info("The cloud rejected the session; signing in again")
            await self._login()

    async def async_fetch_datacenters(self) -> dict[str, Datacenter]:
        """Return the datacenters by code, from the unauthenticated /datacenters."""
        data = await self._post_json(
            const.AUTH_BASE + const.DATACENTERS_PATH, {}, self._auth_headers()
        )
        entries = data.get("data")
        if not isinstance(entries, list):
            raise CloudConnectionError("datacenters: unexpected reply")
        return {
            entry["code"]: Datacenter(
                code=entry["code"],
                api_base=_strip_port(entry.get("apiAddr", "")),
                ws_addr=(entry.get("wsAddr") or "").rstrip("/"),
            )
            for entry in entries
            if isinstance(entry, dict) and entry.get("code")
        }

    async def _device_request(
        self,
        datacenter: Datacenter,
        path: str,
        body: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
        *,
        user_initiated: bool = False,
    ) -> dict[str, Any]:
        """POST to a datacenter with its token; on a 444 re-login once and retry."""
        url = datacenter.api_base + path
        token = self.token_for(datacenter.code)
        headers = {**const.DEVICE_HEADERS, **(extra_headers or {})}
        data = await self._post_json(url, body, {**headers, const.TOKEN_HEADER: token})
        if str(data.get("code")) != const.AUTH_FAIL_CODE:
            return data
        await self.async_relogin(token, user_initiated=user_initiated)
        token = self.token_for(datacenter.code)
        data = await self._post_json(url, body, {**headers, const.TOKEN_HEADER: token})
        if str(data.get("code")) == const.AUTH_FAIL_CODE:
            raise AuthError("The cloud rejected a fresh session")
        return data

    async def async_device_list(
        self, datacenter: Datacenter, *, user_initiated: bool = False
    ) -> list[LockRecord]:
        """Return the locks of the account as the cloud caches them; never wakes a lock."""
        data = await self._device_request(
            datacenter,
            const.DEVICE_LIST_PATH,
            {"uid": self.uid},
            user_initiated=user_initiated,
        )
        records = (data.get("data") or {}).get("wifiList") or []
        return [
            LockRecord.from_record(record)
            for record in records
            if record.get("deviceType") == "LOCK"
        ]

    async def async_unlock(self, datacenter: Datacenter, lock: LockRecord) -> None:
        """Unlock through the gateway; the only request that reaches the lock."""
        params = {
            "esn": lock.esn,
            "mac": lock.mac,
            "masterSn": lock.gateway_esn,
            "userNumberId": lock.user_number_id,
        }
        data = await self._device_request(
            datacenter,
            const.GATEWAY_OPEN_PATH,
            crypto.encrypted_command_body(params),
            {const.ENCRYPT_DATA_HEADER: const.ENCRYPT_DATA_HEADER},
            user_initiated=True,
        )
        if str(data.get("code")) != "200":
            raise CommandError(
                f"Unlock refused: {data.get('msg')} (code {data.get('code')})"
            )
