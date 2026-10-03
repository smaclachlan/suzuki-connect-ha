"""Data update coordinator for Suzuki Connect."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
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
from .pysuzukiconnect.models import localize

from .const import (
    CONF_CONTRACT_ID,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_ENABLE_HEALTH,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_DEVICE_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    HEALTH_REFRESH,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

# Debounce token writes; a login and refresh in quick succession write once.
TOKEN_SAVE_DELAY = 10


def storage_key(entry_id: str) -> str:
    return f"{DOMAIN}.{entry_id}"


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
        # None on entries created before vehicle selection existed: fall back
        # to the first EV, as before.
        self._contract_id: int | None = entry.data.get(CONF_CONTRACT_ID)
        self._enable_health = entry.options.get(CONF_ENABLE_HEALTH, False)
        self._health: VehicleHealth | None = None
        self._health_at: float = 0.0
        self._store: Store[dict] = Store(hass, STORAGE_VERSION, storage_key(entry.entry_id))

        # When HA last successfully synced with the cloud (distinct from the
        # car's own "last reported" time carried in the data).
        self.last_polled: datetime | None = None
        # Poll bookkeeping for diagnostics.
        self.last_attempt: datetime | None = None
        self.last_error: str | None = None
        self.last_latency: float | None = None  # seconds, last successful poll
        self.consecutive_failures = 0

    async def _async_setup(self) -> None:
        """Restore the saved refresh token so a restart refreshes rather than
        doing a forced login (which would log the owner's phone out)."""
        stored = await self._store.async_load() or {}
        self.client.auth.restore_refresh_token(stored.get("refresh_token"))
        self.client.auth.on_tokens_updated = self._schedule_token_save

    def _schedule_token_save(self) -> None:
        self._store.async_delay_save(
            lambda: {"refresh_token": self.client.auth.refresh_token},
            TOKEN_SAVE_DELAY,
        )

    async def _async_update_data(self) -> SuzukiData:
        self.last_attempt = dt_util.utcnow()
        started = time.monotonic()
        try:
            vehicles = await self.client.async_get_vehicles()
            vehicle = self._select_vehicle(vehicles)
            status = await self.client.async_get_status(vehicle.contract_id)
            health = await self._maybe_fetch_health(vehicle.contract_id)
        except SuzukiAuthError as err:
            self._record_failure(err)
            # Credentials no longer work (the client already retries with a
            # forced re-login) -> prompt the user to re-authenticate.
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
        except SuzukiConnectError as err:
            self._record_failure(err)
            raise UpdateFailed(f"Error fetching Suzuki data: {err}") from err
        except UpdateFailed as err:
            self._record_failure(err)
            raise
        self.last_latency = time.monotonic() - started
        self.last_polled = dt_util.utcnow()
        self.last_error = None
        self.consecutive_failures = 0
        return SuzukiData(vehicle=vehicle, status=status, health=health)

    def _record_failure(self, err: Exception) -> None:
        self.last_error = f"{type(err).__name__}: {err}"
        self.consecutive_failures += 1

    def _select_vehicle(self, vehicles: list[Vehicle]) -> Vehicle:
        if self._contract_id is None:
            return next((v for v in vehicles if v.is_ev), vehicles[0])
        for vehicle in vehicles:
            if vehicle.contract_id == self._contract_id:
                return vehicle
        raise UpdateFailed("The selected vehicle is no longer on this Suzuki account")

    def vehicle_time(self, value: datetime | None) -> datetime | None:
        """Make a naive Suzuki timestamp timezone-aware.

        The API reports vehicle times in local time without a zone; we assume
        it matches Home Assistant's configured timezone.
        """
        return localize(value, dt_util.get_default_time_zone())

    @property
    def telemetry_age(self) -> timedelta | None:
        """How old the car's own data was at the last successful poll."""
        if self.data is None or self.last_polled is None:
            return None
        reported = self.vehicle_time(self.data.status.last_updated)
        if reported is None:
            return None
        return max(timedelta(0), self.last_polled - reported)

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
