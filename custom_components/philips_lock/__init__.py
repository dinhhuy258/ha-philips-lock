"""The Philips Smart Lock integration."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .cloud.client import PhilipsCloud
from .cloud.exceptions import AuthError, CloudError
from .const import CONF_AREACODE, CONF_DATACENTER, CONF_EMAIL, CONF_ESN, CONF_PASSWORD
from .coordinator import PhilipsLockCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.LOCK]

type PhilipsLockConfigEntry = ConfigEntry[PhilipsLockCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: PhilipsLockConfigEntry) -> bool:
    """Log in, read the lock from the cloud cache and start the WebSocket listener."""
    client = PhilipsCloud(
        async_get_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        entry.data[CONF_AREACODE],
    )
    esn = entry.data[CONF_ESN]
    try:
        await client.async_login()
        datacenters = await client.async_fetch_datacenters()
        datacenter = datacenters.get(entry.data[CONF_DATACENTER])
        if datacenter is None:
            raise ConfigEntryNotReady(
                f"Datacenter {entry.data[CONF_DATACENTER]} is unknown"
            )
        locks = await client.async_device_list(datacenter, user_initiated=True)
    except AuthError as err:
        raise ConfigEntryAuthFailed(
            "The Philips cloud refused the credentials"
        ) from err
    except CloudError as err:
        raise ConfigEntryNotReady(f"Could not reach the Philips cloud: {err}") from err
    lock = next((lock for lock in locks if lock.esn == esn), None)
    if lock is None:
        raise ConfigEntryNotReady(f"Lock {esn} is not in the account's device list")

    coordinator = PhilipsLockCoordinator(hass, entry, client, datacenter, lock)
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await coordinator.async_start()
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: PhilipsLockConfigEntry
) -> bool:
    """Unload the platforms; the listener task is cancelled with the entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
