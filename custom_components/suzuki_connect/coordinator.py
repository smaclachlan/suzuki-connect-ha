"""Data update coordinator for Suzuki Connect."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .pysuzukiconnect import (
    SuzukiConnectClient,
    SuzukiAuthError,
    SuzukiConnectError,
    Vehicle,
    VehicleHealth,
    VehicleStatus,
)

from datetime import timedelta

from .const import (
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_ENABLE_HEALTH,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_DEVICE_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    HEALTH_REFRESH,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class SuzukiData:
    """Snapshot held by the coordinator."""

    vehicle: Vehicle
    status: VehicleStatus
    health: VehicleHealth | None = None


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
        self._enable_health = entry.options.get(CONF_ENABLE_HEALTH, False)
        self._health: VehicleHealth | None = None
        self._health_at: float = 0.0
        # When HA last successfully synced with the cloud (distinct from the
        # car's own "last reported" time carried in the data).
        self.last_polled: datetime | None = None

    async def _async_update_data(self) -> SuzukiData:
        try:
            vehicle, status = await self.client.async_get_primary_ev_status()
            health = await self._maybe_fetch_health(vehicle.contract_id)
        except SuzukiAuthError as err:
            # Credentials no longer work (the client already retries with a
            # forced re-login) -> prompt the user to re-authenticate.
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
        except SuzukiConnectError as err:
            raise UpdateFailed(f"Error fetching Suzuki data: {err}") from err
        self.last_polled = dt_util.utcnow()
        return SuzukiData(vehicle=vehicle, status=status, health=health)

    async def _maybe_fetch_health(self, contract_id: int) -> VehicleHealth | None:
        """Fetch vehicle health only when opted in, and no more than HEALTH_REFRESH."""
        if not self._enable_health:
            return None
        now = time.monotonic()
        if self._health is None or now - self._health_at >= HEALTH_REFRESH.total_seconds():
            try:
                self._health = await self.client.async_get_vehicle_health(contract_id)
                self._health_at = now
            except SuzukiConnectError as err:
                # Keep the last known value; don't fail the whole update.
                _LOGGER.debug("Vehicle health fetch failed: %s", err)
        return self._health
