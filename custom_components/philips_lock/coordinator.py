"""Coordinator fed by the cloud's WebSocket push."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .cloud.client import Datacenter, LockRecord, PhilipsCloud
from .cloud.exceptions import AuthError, CloudError
from .cloud.realtime import RealtimeListener
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class PhilipsLockCoordinator(DataUpdateCoordinator[LockRecord]):
    """Holds the lock record; the WebSocket listener pushes updates into it.

    There is no polling interval. After every (re)connect the record is read
    again from device/list, a cloud cache that never wakes the lock; while the
    socket is down the data is stale and the entity is unavailable.
    """

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: PhilipsCloud,
        datacenter: Datacenter,
        lock: LockRecord,
    ) -> None:
        """Store the client and the lock read at setup."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {lock.esn}",
            update_interval=None,
        )
        self.client = client
        self.datacenter = datacenter
        self.lock = lock
        self.data = lock
        # The record read at setup may be stale until the socket is up.
        self.last_update_success = False
        self._reauth_requested = False

    async def async_start(self) -> None:
        """Run the WebSocket listener for the life of the config entry."""
        listener = RealtimeListener(
            async_get_clientsession(self.hass),
            self.client,
            self.datacenter,
            self.lock.esn,
        )
        self.config_entry.async_create_background_task(
            self.hass, self._async_run(listener), f"{DOMAIN} websocket {self.lock.esn}"
        )

    async def _async_run(self, listener: RealtimeListener) -> None:
        try:
            await listener.async_listen(
                self._async_on_connect, self._async_on_status, self._async_on_disconnect
            )
        except AuthError as err:
            self.async_set_update_error(err)
            if not self._reauth_requested:
                self._reauth_requested = True
                self.config_entry.async_start_reauth(self.hass)

    async def _async_on_connect(self) -> None:
        """Read the state that may have changed while the socket was down."""
        try:
            locks = await self.client.async_device_list(self.datacenter)
        except CloudError as err:
            self.async_set_update_error(err)
            return
        record = next((lock for lock in locks if lock.esn == self.lock.esn), None)
        if record is None:
            self.async_set_update_error(
                RuntimeError(f"Lock {self.lock.esn} not in device/list")
            )
            return
        self.lock = record
        self.async_set_updated_data(record)

    async def _async_on_status(self, is_locked: bool) -> None:
        """Apply a bolt state pushed by the gateway."""
        self.lock = LockRecord(**{**self.lock.__dict__, "is_locked": is_locked})
        self.async_set_updated_data(self.lock)

    async def _async_on_disconnect(self) -> None:
        """Mark the data stale until the socket is back."""
        self.async_set_update_error(ConnectionError("WebSocket disconnected"))

    async def async_unlock(self) -> None:
        """Unlock through the gateway; raises CloudError on failure."""
        await self.client.async_unlock(self.datacenter, self.lock)
