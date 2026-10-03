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
    SuzukiApiError,
    SuzukiConnectClient,
    SuzukiAuthError,
    SuzukiConnectError,
    Vehicle,
    VehicleHealth,
    VehicleStatus,
)
from .pysuzukiconnect.models import localize

from .const import (
    CONF_CONTRACT_IDS,
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
class VehicleData:
    """One vehicle's latest snapshot."""

    vehicle: Vehicle
    status: VehicleStatus
    health: VehicleHealth | None = None


@dataclass
class SuzukiData:
    """Snapshot held by the coordinator: every selected vehicle, by contract id."""

    vehicles: dict[int, VehicleData]


@dataclass
class VehicleSettings:
    """User-entered per-vehicle values (from the number entities)."""

    battery_capacity: float | None = None   # usable kWh
    charge_target: float | None = None      # %


class SuzukiConnectCoordinator(DataUpdateCoordinator[SuzukiData]):
    """Polls the Suzuki Connect cloud for the account's selected vehicles.

    One coordinator (and so one login) per account: Suzuki allows a single
    active session, so separate clients per vehicle would evict each other.
    """

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
        # Absent on entries created before vehicle selection existed: fall
        # back to the first EV, as before.
        self._contract_ids: list[int] | None = entry.data.get(CONF_CONTRACT_IDS)
        # The selected vehicles from the latest vehicle list, including any
        # whose status call failed, so their entities exist (as unavailable).
        self.vehicles: dict[int, Vehicle] = {}
        self._enable_health = entry.options.get(CONF_ENABLE_HEALTH, False)
        self._health: dict[int, VehicleHealth] = {}
        self._health_at: dict[int, float] = {}
        self.settings: dict[int, VehicleSettings] = {}
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

    async def async_flush_token(self) -> None:
        """Write the refresh token now and stop further saves.

        Called on unload. A debounced save still pending would otherwise land
        after a reload has read the old token (forcing a login that evicts the
        phone), or after removal has deleted the file (leaving a live refresh
        token on disk).
        """
        self.client.auth.on_tokens_updated = None
        await self._store.async_save({"refresh_token": self.client.auth.refresh_token})

    async def _async_update_data(self) -> SuzukiData:
        self.last_attempt = dt_util.utcnow()
        started = time.monotonic()
        try:
            selected = self._select_vehicles(await self.client.async_get_vehicles())
            self.vehicles = {v.contract_id: v for v in selected}
            result: dict[int, VehicleData] = {}
            failures: list[str] = []
            for position, vehicle in enumerate(selected, start=1):
                cid = vehicle.contract_id
                try:
                    status = await self.client.async_get_status(cid)
                except SuzukiApiError as err:
                    # One car failing (e.g. telematics unreachable) shouldn't
                    # blank the others; keep its last snapshot if we have one.
                    # Labelled by name and position, not contract id, as this
                    # reaches logs and diagnostics.
                    failures.append(
                        f"{vehicle.brand or 'Suzuki'} (vehicle {position}): {err}"
                    )
                    if self.data and cid in self.data.vehicles:
                        result[cid] = self.data.vehicles[cid]
                    continue
                health = await self._maybe_fetch_health(cid)
                result[cid] = VehicleData(vehicle=vehicle, status=status, health=health)
            if failures and len(failures) == len(selected):
                raise SuzukiApiError("; ".join(failures))
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
        self.last_error = "; ".join(failures) if failures else None
        self.consecutive_failures = 0
        return SuzukiData(vehicles=result)

    def _record_failure(self, err: Exception) -> None:
        self.last_error = f"{type(err).__name__}: {err}"
        self.consecutive_failures += 1

    def _select_vehicles(self, vehicles: list[Vehicle]) -> list[Vehicle]:
        if self._contract_ids is None:
            return [next((v for v in vehicles if v.is_ev), vehicles[0])]
        selected = [v for v in vehicles if v.contract_id in self._contract_ids]
        if not selected:
            raise UpdateFailed(
                "None of the selected vehicles are on this Suzuki account any more"
            )
        missing = set(self._contract_ids) - {v.contract_id for v in selected}
        if missing:
            _LOGGER.warning(
                "%d selected vehicle(s) are no longer on the Suzuki account", len(missing)
            )
        return selected

    def settings_for(self, contract_id: int) -> VehicleSettings:
        return self.settings.setdefault(contract_id, VehicleSettings())

    def vehicle_time(self, value: datetime | None) -> datetime | None:
        """Make a naive Suzuki timestamp timezone-aware.

        The API reports vehicle times in local time without a zone; we assume
        it matches Home Assistant's configured timezone.
        """
        return localize(value, dt_util.get_default_time_zone())

    def telemetry_age(self, contract_id: int) -> timedelta | None:
        """How old a car's own data was at the last successful poll."""
        if self.data is None or self.last_polled is None:
            return None
        vdata = self.data.vehicles.get(contract_id)
        if vdata is None:
            return None
        reported = self.vehicle_time(vdata.status.last_updated)
        if reported is None:
            return None
        return max(timedelta(0), self.last_polled - reported)

    async def _maybe_fetch_health(self, contract_id: int) -> VehicleHealth | None:
        """Fetch vehicle health only when opted in, and no more than HEALTH_REFRESH."""
        if not self._enable_health:
            return None
        now = time.monotonic()
        last = self._health_at.get(contract_id)
        if last is None or now - last >= HEALTH_REFRESH.total_seconds():
            try:
                self._health[contract_id] = await self.client.async_get_vehicle_health(
                    contract_id
                )
                self._health_at[contract_id] = now
            except SuzukiConnectError as err:
                # Keep the last known value; don't fail the whole update.
                _LOGGER.debug("Vehicle health fetch failed: %s", err)
        return self._health.get(contract_id)
