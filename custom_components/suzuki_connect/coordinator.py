"""Data update coordinator for Suzuki Connect."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .pysuzukiconnect import (
    SuzukiConnectClient,
    SuzukiAuthError,
    SuzukiConnectError,
    Vehicle,
    VehicleStatus,
)

from datetime import timedelta

from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_DEVICE_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class SuzukiData:
    """Snapshot held by the coordinator."""

    vehicle: Vehicle
    status: VehicleStatus


class SuzukiConnectCoordinator(DataUpdateCoordinator[SuzukiData]):
    """Polls the Suzuki Connect cloud for one vehicle's status."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        minutes = entry.options.get(CONF_SCAN_INTERVAL_MINUTES)
        interval = timedelta(minutes=minutes) if minutes else DEFAULT_SCAN_INTERVAL
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=interval,
            config_entry=entry,
        )
        self.client = SuzukiConnectClient(
            async_get_clientsession(hass),
            entry.data["email"],
            entry.data["password"],
            device_id=entry.data.get(CONF_DEVICE_ID),
            device_name=entry.data.get(CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME),
        )

    async def _async_update_data(self) -> SuzukiData:
        try:
            vehicle, status = await self.client.async_get_primary_ev_status()
        except SuzukiAuthError as err:
            # Surface as auth failure so HA can trigger reauth if persistent.
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except SuzukiConnectError as err:
            raise UpdateFailed(f"Error fetching Suzuki data: {err}") from err
        return SuzukiData(vehicle=vehicle, status=status)
