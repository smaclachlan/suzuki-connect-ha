"""Data update coordinator for Suzuki Connect."""
from __future__ import annotations

import asyncio
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
    ChargingHistory,
    DrivingHistory,
    Schedules,
    Subscription,
    SuzukiApiError,
    SuzukiConnectClient,
    SuzukiAuthError,
    SuzukiConnectError,
    Vehicle,
    VehicleHealth,
    VehicleStatus,
)
from .pysuzukiconnect.models import ChargeSession, Trip, localize

from .const import (
    CONF_CONTRACT_IDS,
    CONF_DEVICE_ID,
    CONF_DEVICE_NAME,
    CONF_ENABLE_EXTENDED,
    CONF_ENABLE_HEALTH,
    CONF_SLOW_INTERVAL_MINUTES,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_DEVICE_NAME,
    DEFAULT_SLOW_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    HEALTH_REFRESH,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

# Debounce token writes; a login and refresh in quick succession write once.
TOKEN_SAVE_DELAY = 10
# Months of trips the calendar may fetch in one request (e.g. a year view).
MAX_TRIP_MONTHS_PER_REQUEST = 12
# Live fields missing or blank in a response keep their last value for up to
# this many consecutive polls (Suzuki intermittently drops fields, so 1-minute
# polls flickered to unknown), then go unknown. Counted in polls, not time, so
# the hold scales with the poll interval.
FIELD_HOLD_POLLS = 3
# Fields whose absence is meaningful, so never held.
NEVER_HELD = frozenset({"chargerConnected_st"})


def _months_between(start: datetime, end: datetime) -> list[str]:
    """yyyy-MM for every month overlapping [start, end)."""
    months = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def storage_key(entry_id: str) -> str:
    return f"{DOMAIN}.{entry_id}"


@dataclass
class ExtendedData:
    """One vehicle's rarely-refreshed extra data; each part is None until it
    has been fetched successfully once."""

    trips: list[Trip] | None = None        # newest first, this and last month
    driving: DrivingHistory | None = None  # account-level monthly report
    charging: ChargingHistory | None = None
    charge_schedules: Schedules | None = None
    climate_schedules: Schedules | None = None
    subscription: Subscription | None = None


@dataclass
class VehicleData:
    """One vehicle's latest snapshot."""

    vehicle: Vehicle
    status: VehicleStatus
    health: VehicleHealth | None = None
    extended: ExtendedData | None = None


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
        self._enable_extended = entry.options.get(CONF_ENABLE_EXTENDED, False)
        self._extended: dict[int, ExtendedData] = {}
        self._extended_at: float | None = None
        # Driving history by month (yyyy-MM), account-wide. This and last month
        # are refreshed on the slow path; older months are fetched when the
        # trip calendar is browsed, then kept (they no longer change).
        self._trip_months: dict[str, DrivingHistory] = {}
        self._trip_months_lock = asyncio.Lock()
        self._first_trip_month: str | None = None
        # Charging sessions seen so far, per car, keyed by start time. The
        # history endpoint returns only the latest few, so this builds up.
        self._charge_log: dict[int, dict[datetime, ChargeSession]] = {}
        # Per car: live field -> [last value, consecutive polls missing].
        self._fields: dict[int, dict[str, list]] = {}
        # Per car, for diagnostics: fields currently held, and per field how
        # often it has been missing since startup and its longest run.
        self.held_fields: dict[int, list[str]] = {}
        self.field_gaps: dict[int, dict[str, dict[str, int]]] = {}
        slow_minutes = entry.options.get(CONF_SLOW_INTERVAL_MINUTES)
        self._slow_interval = (
            timedelta(minutes=slow_minutes) if slow_minutes else DEFAULT_SLOW_INTERVAL
        )
        # The account's vehicle list, refreshed on the slow interval.
        self._vehicle_list: list[Vehicle] | None = None
        self._vehicle_list_at: float | None = None
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
        self.client.auth.restore_tokens(
            stored.get("refresh_token"), stored.get("access_token")
        )
        self.client.auth.on_tokens_updated = self._schedule_token_save

    def _schedule_token_save(self) -> None:
        self._store.async_delay_save(self._token_data, TOKEN_SAVE_DELAY)

    def _token_data(self) -> dict:
        # The access token is kept because a refresh must send it.
        auth = self.client.auth
        return {"refresh_token": auth.refresh_token, "access_token": auth.last_access_token}

    async def async_flush_token(self) -> None:
        """Write the refresh token now and stop further saves.

        Called on unload. A debounced save still pending would otherwise land
        after a reload has read the old token (forcing a login that evicts the
        phone), or after removal has deleted the file (leaving a live refresh
        token on disk).
        """
        self.client.auth.on_tokens_updated = None
        await self._store.async_save(self._token_data())

    async def _async_update_data(self) -> SuzukiData:
        self.last_attempt = dt_util.utcnow()
        started = time.monotonic()
        try:
            selected = self._select_vehicles(await self._get_vehicle_list())
            self.vehicles = {v.contract_id: v for v in selected}
            result: dict[int, VehicleData] = {}
            failures: list[str] = []
            for position, vehicle in enumerate(selected, start=1):
                cid = vehicle.contract_id
                try:
                    status = self._hold_missing_fields(
                        cid, await self.client.async_get_status(cid)
                    )
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
            await self._maybe_fetch_extended(list(result))
            for cid, vdata in result.items():
                vdata.extended = self._extended.get(cid)
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

    def _hold_missing_fields(self, cid: int, status: VehicleStatus) -> VehicleStatus:
        """Fill fields missing or blank in this response from recent polls."""
        seen = self._fields.setdefault(cid, {})
        gaps = self.field_gaps.setdefault(cid, {})
        received = {k: v for k, v in status.raw.items() if v not in (None, "")}
        for key, value in received.items():
            seen[key] = [value, 0]
        held = {}
        for key, entry in list(seen.items()):
            if key in received or key in NEVER_HELD:
                continue
            entry[1] += 1
            gap = gaps.setdefault(key, {"missing": 0, "longest_run": 0})
            gap["missing"] += 1
            gap["longest_run"] = max(gap["longest_run"], entry[1])
            if entry[1] <= FIELD_HOLD_POLLS:
                held[key] = entry[0]
            else:
                del seen[key]
        self.held_fields[cid] = sorted(held)
        if not held:
            return status
        return VehicleStatus.from_dashboard(
            {**status.raw_meta, "user_data": {**status.raw, **held}}
        )

    async def _get_vehicle_list(self) -> list[Vehicle]:
        """The account's vehicles, fetched at startup and then on the slow
        interval (they rarely change), not on every live poll.

        If a scheduled refresh fails, the cached list is used and the poll
        carries on; only an auth failure or having no list at all fails it.
        """
        now = time.monotonic()
        if (
            self._vehicle_list is not None
            and self._vehicle_list_at is not None
            and now - self._vehicle_list_at < self._slow_interval.total_seconds()
        ):
            return self._vehicle_list
        try:
            vehicles = await self.client.async_get_vehicles()
        except SuzukiAuthError:
            raise
        except SuzukiConnectError as err:
            if self._vehicle_list is None:
                raise
            _LOGGER.debug("Vehicle list refresh failed, using cached list: %s", err)
            return self._vehicle_list
        self._vehicle_list, self._vehicle_list_at = vehicles, now
        return vehicles

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

    async def _maybe_fetch_extended(self, contract_ids: list[int]) -> None:
        """Fetch trips, charging history and schedules (and read the
        subscription from the vehicle list) when opted
        in, at most once per slow interval.

        Each part is fetched independently: a failure (e.g. an endpoint the
        car's plan doesn't offer) keeps that part's last value and never fails
        the poll.
        """
        if not self._enable_extended or not contract_ids:
            return
        now = time.monotonic()
        if (
            self._extended_at is not None
            and now - self._extended_at < self._slow_interval.total_seconds()
        ):
            return
        succeeded = False

        async def fetch(label: str, call):
            nonlocal succeeded
            try:
                value = await call
            except SuzukiConnectError as err:
                _LOGGER.debug("Fetching %s failed: %s", label, err)
                return None
            succeeded = True
            return value

        history = await self._fetch_trips(fetch)
        for cid in contract_ids:
            ext = self._extended.setdefault(cid, ExtendedData())
            # Already in the vehicle list; the subscription endpoint itself
            # returned nothing on a live account.
            if (vehicle := self.vehicles.get(cid)) is not None:
                ext.subscription = Subscription.from_details(
                    vehicle.raw.get("subscriptionDetails")
                )
            if history is not None:
                ext.driving = history
                ext.trips = history.for_contract(cid, only_vehicle=len(contract_ids) == 1)
            for attr, label, call in (
                ("charging", "charging history", self.client.async_get_charging_history),
                ("charge_schedules", "charge schedules", self.client.async_get_charge_schedules),
                ("climate_schedules", "climate schedules", self.client.async_get_climate_schedules),
            ):
                value = await fetch(label, call(cid))
                if value is not None:
                    setattr(ext, attr, value)
            if ext.charging is not None:
                log = self._charge_log.setdefault(cid, {})
                for session in ext.charging.sessions:
                    if session.time is not None:
                        log[session.time] = session
        # If everything failed (e.g. a transient outage), try again next poll.
        if succeeded:
            self._extended_at = now

    async def _fetch_trips(self, fetch) -> DrivingHistory | None:
        """This month's trips plus last month's, so the latest trip survives
        the start of a new month."""
        today = dt_util.now().date()
        this_month = today.strftime("%Y-%m")
        last_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        current = await fetch("driving history", self.client.async_get_driving_history(this_month))
        previous = await fetch("driving history", self.client.async_get_driving_history(last_month))
        for month, history in ((this_month, current), (last_month, previous)):
            if history is not None:
                self._trip_months[month] = history
                self._first_trip_month = history.first_month or self._first_trip_month
        if current is None:
            return None
        return current.merged(previous) if previous is not None else current

    async def async_trips_between(
        self, contract_id: int, start: datetime, end: datetime
    ) -> list[Trip]:
        """One car's trips overlapping [start, end), for the trip calendar.

        Months not yet cached are fetched on demand (at most
        MAX_TRIP_MONTHS_PER_REQUEST per call, never before the account's
        first trip month or after this month). A failed fetch is skipped.
        """
        months = _months_between(dt_util.as_local(start), dt_util.as_local(end))
        this_month = dt_util.now().strftime("%Y-%m")
        wanted = [
            m for m in months
            if m <= this_month and (self._first_trip_month is None or m >= self._first_trip_month)
        ]
        async with self._trip_months_lock:
            missing = [m for m in wanted if m not in self._trip_months]
            for month in missing[:MAX_TRIP_MONTHS_PER_REQUEST]:
                try:
                    self._trip_months[month] = await self.client.async_get_driving_history(month)
                except SuzukiConnectError as err:
                    _LOGGER.debug("Fetching trips for %s failed: %s", month, err)
        only_vehicle = len(self.vehicles) <= 1
        trips = []
        for month in wanted:
            if (history := self._trip_months.get(month)) is None:
                continue
            for trip in history.for_contract(contract_id, only_vehicle=only_vehicle):
                t_start = self.vehicle_time(trip.start)
                t_end = self.vehicle_time(trip.end) or t_start
                if t_start is not None and t_start < end and t_end >= start:
                    trips.append(trip)
        return trips

    def charge_sessions(self, contract_id: int) -> list[ChargeSession]:
        """Every charging session seen for a car since startup, newest first."""
        log = self._charge_log.get(contract_id, {})
        return [log[t] for t in sorted(log, reverse=True)]

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
