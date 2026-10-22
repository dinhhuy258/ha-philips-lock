"""Lock platform of the Philips Smart Lock integration."""

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import PhilipsLockConfigEntry
from .cloud.exceptions import CloudError
from .const import DOMAIN, MANUFACTURER
from .coordinator import PhilipsLockCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PhilipsLockConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the lock entity of the entry."""
    async_add_entities([PhilipsLock(entry.runtime_data)])


class PhilipsLock(CoordinatorEntity[PhilipsLockCoordinator], LockEntity):
    """A Philips lock behind a Wi-Fi gateway."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, coordinator: PhilipsLockCoordinator) -> None:
        """Describe the entity and its device from the lock record."""
        super().__init__(coordinator)
        lock = coordinator.lock
        self._attr_unique_id = lock.esn
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, lock.esn)},
            manufacturer=MANUFACTURER,
            model=lock.model or None,
            name=lock.name,
            serial_number=lock.esn,
            sw_version=lock.sw_version or None,
        )

    @property
    def is_locked(self) -> bool | None:
        """Return the bolt state pushed by the cloud."""
        return self.coordinator.data.is_locked

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock through the gateway; the state follows the cloud's push."""
        try:
            await self.coordinator.async_unlock()
        except CloudError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="unlock_failed",
                translation_placeholders={
                    "name": self.coordinator.lock.name,
                    "reason": str(err),
                },
            ) from err

    async def async_lock(self, **kwargs: Any) -> None:
        """Refuse: the lock relocks itself about 5 s after unlocking."""
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="lock_not_supported",
            translation_placeholders={"name": self.coordinator.lock.name},
        )
