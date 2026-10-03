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

    async def _validate(self, data: dict[str, Any]) -> tuple[str | None, str | None]:
        """Try to log in. Return (error_key, vehicle_title); error_key None on success."""
        client = SuzukiConnectClient(
            async_get_clientsession(self.hass),
            data[CONF_EMAIL],
            data[CONF_PASSWORD],
            device_id=data[CONF_DEVICE_ID],
            device_name=data.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME),
        )
        try:
            vehicles = await client.async_get_vehicles()
        except SuzukiAnotherActiveLogin as err:
            _LOGGER.warning("Suzuki: another active login: %s", err)
            return "another_active_login", None
        except SuzukiAuthError as err:
            _LOGGER.warning("Suzuki auth failed: %s", err)
            return "invalid_auth", None
        except SuzukiNoVehicleError:
            return "no_vehicle", None
        except SuzukiConnectError as err:
            _LOGGER.warning("Suzuki connect error: %s", err)
            return "cannot_connect", None
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Suzuki: unexpected error during setup")
            return "unknown", None
        return None, next((v.brand for v in vehicles if v.brand), "Suzuki")

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            await self.async_set_unique_id(email.lower())
            self._abort_if_unique_id_configured()
            data = {
                CONF_EMAIL: email,
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                # Stable, persisted device id so HA keeps one of the 5 device slots.
                CONF_DEVICE_ID: str(uuid.uuid4()),
                CONF_DEVICE_NAME: user_input.get(CONF_DEVICE_NAME) or DEFAULT_DEVICE_NAME,
            }
            error, title = await self._validate(data)
            if error is None:
                return self.async_create_entry(title=title, data=data)
            errors["base"] = error

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

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Triggered when the stored credentials stop working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            error, _ = await self._validate(data)
            if error is None:
                return self.async_update_reload_and_abort(entry, data=data)
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
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
