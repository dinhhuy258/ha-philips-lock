"""Tests for the WebSocket listener."""

import asyncio
from collections.abc import Iterable
import json
from typing import Any
from unittest.mock import AsyncMock

import aiohttp
import pytest

from custom_components.philips_lock.cloud import realtime
from custom_components.philips_lock.cloud.client import Datacenter
from custom_components.philips_lock.cloud.exceptions import (
    AuthError,
    CloudConnectionError,
)

ESN = "LOCK000000001"
NA = Datacenter(
    code="PhilipsNorthAmerica",
    api_base="https://api.example.com",
    ws_addr="wss://ws.example.com",
)


def _frame(func: str, esn: str = ESN, **params: Any) -> str:
    return json.dumps(
        {
            "msgId": 1,
            "func": func,
            "body": {
                "esn": esn,
                "eventparams": params,
                "devtype": "gateway",
                "eventtype": "gwBleEvent",
            },
        }
    )


def test_parse_frame_reads_open_status_of_our_lock() -> None:
    """A bleAction frame of the lock yields its bolt state."""
    assert (
        realtime.parse_frame(_frame("bleAction", openStatus=2, power=100), ESN) is False
    )
    assert realtime.parse_frame(_frame("bleAction", openStatus=1), ESN) is True


def test_parse_frame_ignores_other_frames() -> None:
    """Other locks, other functions, unknown statuses and junk yield None."""
    assert (
        realtime.parse_frame(_frame("bleAction", esn="OTHER", openStatus=2), ESN)
        is None
    )
    assert realtime.parse_frame(_frame("opeBle", eventCode=2), ESN) is None
    assert realtime.parse_frame(_frame("bleAction", openStatus=7), ESN) is None
    assert realtime.parse_frame("not json", ESN) is None
    assert realtime.parse_frame("[]", ESN) is None


class FakeSocket:
    """A WebSocket that yields prepared text frames and then closes."""

    def __init__(self, frames: Iterable[str]) -> None:
        self._frames = list(frames)
        self.close_code = 1000

    async def __aenter__(self) -> FakeSocket:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def __aiter__(self) -> FakeSocket:
        return self

    async def __anext__(self) -> aiohttp.WSMessage:
        if not self._frames:
            raise StopAsyncIteration
        return aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, self._frames.pop(0), None)


class FakeSession:
    """Hands out one prepared socket or failure per connection attempt."""

    def __init__(self, attempts: list[FakeSocket | Exception]) -> None:
        self.attempts = attempts
        self.calls: list[dict[str, Any]] = []

    def ws_connect(self, url: str, **kwargs: Any) -> FakeSocket:
        self.calls.append({"url": url, **kwargs})
        attempt = self.attempts.pop(0)
        if isinstance(attempt, Exception):
            raise attempt
        return attempt


class FakeClient:
    """The slice of PhilipsCloud the listener uses."""

    def __init__(self, relogin_error: Exception | None = None) -> None:
        self.uid = "uid-1"
        self.relogins: list[str] = []
        self.relogin_error = relogin_error

    def token_for(self, code: str) -> str:
        return "token-na"

    async def async_relogin(self, token: str, *, user_initiated: bool = False) -> None:
        self.relogins.append(token)
        if self.relogin_error is not None:
            raise self.relogin_error


@pytest.fixture(autouse=True)
def no_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reconnect immediately."""
    monkeypatch.setattr(realtime.const, "RECONNECT_DELAY", 0)
    monkeypatch.setattr(realtime.const, "RECONNECT_DELAY_MAX", 0)


async def _run(
    listener: realtime.RealtimeListener,
    on_connect: AsyncMock,
    on_status: AsyncMock,
    on_disconnect: AsyncMock,
    stop_after: int,
) -> None:
    """Run the listener until on_disconnect was called stop_after times."""
    done = asyncio.Event()

    async def disconnect() -> None:
        await on_disconnect()
        if on_disconnect.await_count >= stop_after:
            done.set()

    task = asyncio.create_task(listener.async_listen(on_connect, on_status, disconnect))
    await asyncio.wait_for(done.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_listener_connects_with_token_and_delivers_states() -> None:
    """The handshake carries the uid and the token; frames become states."""
    session = FakeSession(
        [
            FakeSocket(
                [
                    _frame("bleAction", openStatus=2),
                    _frame("opeBle", eventCode=1),
                    _frame("bleAction", openStatus=1),
                ]
            )
        ]
    )
    client = FakeClient()
    listener = realtime.RealtimeListener(session, client, NA, ESN)
    on_connect, on_status, on_disconnect = AsyncMock(), AsyncMock(), AsyncMock()
    session.attempts.append(asyncio.CancelledError())
    await _run(listener, on_connect, on_status, on_disconnect, stop_after=1)
    assert session.calls[0]["url"] == "wss://ws.example.com/?client_id=app:uid-1"
    assert session.calls[0]["protocols"] == ("token-na",)
    assert session.calls[0]["heartbeat"] == realtime.const.WS_HEARTBEAT
    on_connect.assert_awaited_once()
    assert [call.args[0] for call in on_status.await_args_list] == [False, True]
    on_disconnect.assert_awaited_once()


async def test_listener_reconnects_after_a_drop() -> None:
    """A closed socket is reopened; on_connect runs again."""
    session = FakeSession([FakeSocket([]), FakeSocket([]), asyncio.CancelledError()])
    listener = realtime.RealtimeListener(session, FakeClient(), NA, ESN)
    on_connect, on_status, on_disconnect = AsyncMock(), AsyncMock(), AsyncMock()
    await _run(listener, on_connect, on_status, on_disconnect, stop_after=2)
    assert on_connect.await_count == 2


async def test_refused_handshake_asks_for_a_relogin_and_retries() -> None:
    """A failed handshake triggers a rate-limited re-login; a transient error keeps retrying."""
    session = FakeSession(
        [aiohttp.ClientError("502"), FakeSocket([]), asyncio.CancelledError()]
    )
    client = FakeClient(relogin_error=CloudConnectionError("re-login rate-limited"))
    listener = realtime.RealtimeListener(session, client, NA, ESN)
    on_connect, on_status, on_disconnect = AsyncMock(), AsyncMock(), AsyncMock()
    await _run(listener, on_connect, on_status, on_disconnect, stop_after=1)
    # One re-login after the refused handshake, one after the socket that
    # closed at once (a young drop also hints at a displaced token).
    assert client.relogins == ["token-na", "token-na"]
    assert on_connect.await_count == 1


async def test_auth_error_ends_the_loop() -> None:
    """A re-login refused by the cloud propagates so the owner can reauth."""
    session = FakeSession([aiohttp.ClientError("502")])
    client = FakeClient(relogin_error=AuthError("Login refused"))
    listener = realtime.RealtimeListener(session, client, NA, ESN)
    with pytest.raises(AuthError):
        await asyncio.wait_for(
            listener.async_listen(AsyncMock(), AsyncMock(), AsyncMock()), 2
        )
