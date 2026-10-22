"""Config flow of the Philips Smart Lock integration."""

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .cloud.client import LockRecord, PhilipsCloud
from .cloud.exceptions import AuthError, CloudError
from .const import (
    CONF_AREACODE,
    CONF_DATACENTER,
    CONF_EMAIL,
    CONF_ESN,
    CONF_PASSWORD,
    DEFAULT_AREACODE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL)
        ),
        vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR,
        vol.Required(CONF_AREACODE, default=DEFAULT_AREACODE): TextSelector(),
    }
)
STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR})


def _client(hass: HomeAssistant, data: Mapping[str, Any]) -> PhilipsCloud:
    return PhilipsCloud(
        async_get_clientsession(hass),
        data[CONF_EMAIL],
        data[CONF_PASSWORD],
        data[CONF_AREACODE],
    )


async def _async_find_locks(client: PhilipsCloud) -> list[LockRecord]:
    """Log in and return the locks the account can use, one record per lock.

    Every datacenter the login handed a token for is listed; a datacenter that
    fails or echoes a lock homed elsewhere is skipped, and a lock is kept only
    when its own datacenter is one the account has a token for.
    """
    await client.async_login()
    datacenters = await client.async_fetch_datacenters()
    codes = client.datacenter_codes()
    locks: dict[str, LockRecord] = {}
    for code in codes:
        datacenter = datacenters.get(code)
        if datacenter is None or not datacenter.api_base:
            continue
        try:
            records = await client.async_device_list(datacenter, user_initiated=True)
        except CloudError as err:
            _LOGGER.debug("device/list on %s failed: %s", code, err)
            continue
        for record in records:
            if record.datacenter in codes and (
                record.esn not in locks or record.datacenter == code
            ):
                locks[record.esn] = record
    return list(locks.values())


class PhilipsLockConfigFlow(ConfigFlow, domain=DOMAIN):
    """Sign in to the Philips Home Access cloud and pick a lock."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Keep the credentials and the locks between the steps."""
        self._data: dict[str, Any] = {}
        self._locks: list[LockRecord] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the credentials and list the locks of the account."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data = {**user_input, CONF_EMAIL: user_input[CONF_EMAIL].strip()}
            try:
                self._locks = await _async_find_locks(_client(self.hass, self._data))
            except AuthError:
                errors["base"] = "invalid_auth"
            except CloudError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error while signing in")
                errors["base"] = "unknown"
            else:
                if not self._locks:
                    errors["base"] = "no_locks"
                elif len(self._locks) == 1:
                    return await self._async_create(self._locks[0])
                else:
                    return await self.async_step_pick()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose one of several locks."""
        if user_input is not None:
            lock = next(
                lock for lock in self._locks if lock.esn == user_input[CONF_ESN]
            )
            return await self._async_create(lock)
        options = [
            SelectOptionDict(value=lock.esn, label=f"{lock.name} ({lock.esn})")
            for lock in self._locks
        ]
        return self.async_show_form(
            step_id="pick",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ESN): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.LIST
                        )
                    )
                }
            ),
        )

    async def _async_create(self, lock: LockRecord) -> ConfigFlowResult:
        await self.async_set_unique_id(lock.esn)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=lock.name,
            data={**self._data, CONF_ESN: lock.esn, CONF_DATACENTER: lock.datacenter},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after the cloud refused the credentials."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password again and check it with a login."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            try:
                await _client(self.hass, data).async_login()
            except AuthError:
                errors["base"] = "invalid_auth"
            except CloudError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"email": entry.data[CONF_EMAIL]},
        )
