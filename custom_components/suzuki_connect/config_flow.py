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
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .pysuzukiconnect import (
    SuzukiConnectClient,
    SuzukiAnotherActiveLogin,
    SuzukiAuthError,
    SuzukiConnectError,
    SuzukiNoVehicleError,
    Vehicle,
)

from .const import (
    CONF_CONTRACT_IDS,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_ENABLE_EXTENDED,
    CONF_ENABLE_HEALTH,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_DEVICE_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL_MINUTES,
    MIN_SCAN_INTERVAL_MINUTES,
)


def _vehicle_label(vehicle: Vehicle) -> str:
    """Human label for the vehicle picker; only the VIN's tail is shown."""
    name = vehicle.brand or "Suzuki"
    if vehicle.vin and len(vehicle.vin) >= 4:
        return f"{name} (VIN …{vehicle.vin[-4:]})"
    return f"{name} (contract {vehicle.contract_id})"

_LOGGER = logging.getLogger(__name__)


class SuzukiConnectConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1
    MINOR_VERSION = 2

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._vehicles: list[Vehicle] = []

    async def _validate(
        self, data: dict[str, Any]
    ) -> tuple[str | None, list[Vehicle]]:
        """Try to log in. Return (error_key, vehicles); error_key None on success."""
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
            return "another_active_login", []
        except SuzukiAuthError as err:
            _LOGGER.warning("Suzuki auth failed: %s", err)
            return "invalid_auth", []
        except SuzukiNoVehicleError:
            return "no_vehicle", []
        except SuzukiConnectError as err:
            _LOGGER.warning("Suzuki connect error: %s", err)
            return "cannot_connect", []
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Suzuki: unexpected error during setup")
            return "unknown", []
        return None, vehicles

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
            error, vehicles = await self._validate(data)
            if error is None:
                self._data = data
                self._vehicles = vehicles
                if len(vehicles) == 1:
                    return self._create_entry(vehicles)
                return await self.async_step_vehicle()
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

    async def async_step_vehicle(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick which of the account's vehicles to add (one or more)."""
        by_id = {str(v.contract_id): v for v in self._vehicles}
        errors: dict[str, str] = {}
        if user_input is not None:
            chosen = [by_id[cid] for cid in user_input[CONF_CONTRACT_IDS]]
            if chosen:
                return self._create_entry(chosen)
            errors["base"] = "no_vehicle_selected"
        default = [str(v.contract_id) for v in self._vehicles if v.is_ev] or [
            str(self._vehicles[0].contract_id)
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_CONTRACT_IDS, default=default): cv.multi_select(
                    {cid: _vehicle_label(v) for cid, v in by_id.items()}
                ),
            }
        )
        return self.async_show_form(step_id="vehicle", data_schema=schema, errors=errors)

    def _create_entry(self, vehicles: list[Vehicle]) -> ConfigFlowResult:
        title = (
            vehicles[0].brand or "Suzuki" if len(vehicles) == 1 else "Suzuki Connect"
        )
        return self.async_create_entry(
            title=title,
            data={**self._data, CONF_CONTRACT_IDS: [v.contract_id for v in vehicles]},
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

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL_MINUTES,
                    default=options.get(
                        CONF_SCAN_INTERVAL_MINUTES,
                        int(DEFAULT_SCAN_INTERVAL.total_seconds() // 60),
                    ),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(
                        min=MIN_SCAN_INTERVAL_MINUTES, max=MAX_SCAN_INTERVAL_MINUTES
                    ),
                ),
                vol.Required(
                    CONF_ENABLE_HEALTH,
                    default=options.get(CONF_ENABLE_HEALTH, False),
                ): bool,
                vol.Required(
                    CONF_ENABLE_EXTENDED,
                    default=options.get(CONF_ENABLE_EXTENDED, False),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
