"""Lock state pushed by the cloud over a WebSocket."""

import asyncio
from collections.abc import Awaitable, Callable
import json
import logging
import time
from typing import Protocol

import aiohttp

from . import const
from .client import Datacenter
from .exceptions import AuthError, CloudError

_LOGGER = logging.getLogger(__name__)

OnConnect = Callable[[], Awaitable[None]]
OnStatus = Callable[[bool], Awaitable[None]]
OnDisconnect = Callable[[], Awaitable[None]]


class _Client(Protocol):
    uid: str

    def token_for(self, code: str) -> str: ...

    async def async_relogin(
        self, token: str, *, user_initiated: bool = False
    ) -> None: ...


def parse_frame(text: str, esn: str) -> bool | None:
    """Return the bolt state in a bleAction frame of the lock esn, else None.

    The gateway reports the lock's full state (openStatus, power, ...) after
    every bolt movement; only openStatus is used.
    """
    try:
        frame = json.loads(text)
    except ValueError:
        return None
    if not isinstance(frame, dict) or frame.get("func") != "bleAction":
        return None
    body = frame.get("body") or {}
    if body.get("esn") != esn:
        return None
    open_status = (body.get("eventparams") or {}).get("openStatus")
    if open_status == const.OPEN_STATUS_LOCKED:
        return True
    if open_status == const.OPEN_STATUS_UNLOCKED:
        return False
    return None


class RealtimeListener:
    """Keeps the WebSocket of one datacenter open and reports one lock's state."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        client: _Client,
        datacenter: Datacenter,
        esn: str,
    ) -> None:
        """Remember where to connect and which lock to watch."""
        self._session = session
        self._client = client
        self._datacenter = datacenter
        self._esn = esn

    async def async_listen(
        self, on_connect: OnConnect, on_status: OnStatus, on_disconnect: OnDisconnect
    ) -> None:
        """Run until cancelled, or until a re-login fails with AuthError.

        on_connect runs after every successful handshake, on_status for every
        bolt state of the lock, on_disconnect after every drop of an open socket.
        """
        delay = const.RECONNECT_DELAY
        while True:
            connected_at: float | None = None
            token = self._client.token_for(self._datacenter.code)
            url = f"{self._datacenter.ws_addr}/?client_id=app:{self._client.uid}"
            try:
                async with self._session.ws_connect(
                    url, protocols=(token,), heartbeat=const.WS_HEARTBEAT
                ) as socket:
                    connected_at = time.monotonic()
                    _LOGGER.debug("WebSocket connected")
                    await on_connect()
                    async for message in socket:
                        if message.type != aiohttp.WSMsgType.TEXT:
                            continue
                        is_locked = parse_frame(message.data, self._esn)
                        if is_locked is not None:
                            await on_status(is_locked)
                    _LOGGER.debug(
                        "WebSocket closed by the server (%s)", socket.close_code
                    )
            except (aiohttp.ClientError, TimeoutError) as err:
                _LOGGER.debug("WebSocket dropped: %s", err)
            if connected_at is not None:
                await on_disconnect()
            if (
                connected_at is not None
                and time.monotonic() - connected_at >= const.STABLE_AFTER
            ):
                delay = const.RECONNECT_DELAY
            else:
                # Refused or dropped at once: the token may have been displaced
                # by another login. The re-login is rate-limited in the client.
                try:
                    await self._client.async_relogin(token)
                except AuthError:
                    raise
                except CloudError as err:
                    _LOGGER.debug("Re-login not possible yet: %s", err)
            _LOGGER.debug("WebSocket reconnecting in %s s", delay)
            await asyncio.sleep(delay)
            delay = (
                min(max(delay, 1) * 2, const.RECONNECT_DELAY_MAX)
                if const.RECONNECT_DELAY_MAX
                else 0
            )
