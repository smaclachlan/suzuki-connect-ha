"""Config and options flow for Suzuki Connect."""
from __future__ import annotations

import logging
import uuid
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .pysuzukiconnect import (
    SuzukiConnectClient,
    SuzukiAnotherActiveLogin,
    SuzukiAuthError,
    SuzukiConnectError,
    SuzukiNoVehicleError,
)

from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_DEVICE_NAME,
    DOMAIN,
    MIN_SCAN_INTERVAL_MINUTES,
)

_LOGGER = logging.getLogger(__name__)


class SuzukiConnectConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            device_name = user_input.get(CONF_DEVICE_NAME) or DEFAULT_DEVICE_NAME
            # Stable, persisted device id so HA keeps one of the 5 device slots.
            device_id = str(uuid.uuid4())

            await self.async_set_unique_id(email.lower())
            self._abort_if_unique_id_configured()

            client = SuzukiConnectClient(
                async_get_clientsession(self.hass),
                email,
                user_input[CONF_PASSWORD],
                device_id=device_id,
                device_name=device_name,
            )
            try:
                vehicles = await client.async_get_vehicles()
            except SuzukiAnotherActiveLogin as err:
                _LOGGER.warning("Suzuki: another active login: %s", err)
                errors["base"] = "another_active_login"
            except SuzukiAuthError as err:
                _LOGGER.warning("Suzuki auth failed: %s", err)
                errors["base"] = "invalid_auth"
            except SuzukiNoVehicleError:
                errors["base"] = "no_vehicle"
            except SuzukiConnectError as err:
                _LOGGER.warning("Suzuki connect error: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Suzuki: unexpected error during setup")
                errors["base"] = "unknown"
            else:
                title = next(
                    (v.brand for v in vehicles if v.brand), "Suzuki"
                )
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_DEVICE_ID: device_id,
                        CONF_DEVICE_NAME: device_name,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_DEVICE_NAME, default=DEFAULT_DEVICE_NAME): str,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return SuzukiConnectOptionsFlow()


class SuzukiConnectOptionsFlow(OptionsFlow):
    """Let the user tune the poll interval."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(CONF_SCAN_INTERVAL_MINUTES, 15)
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL_MINUTES, default=current
                ): vol.All(int, vol.Range(min=MIN_SCAN_INTERVAL_MINUTES, max=240)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
