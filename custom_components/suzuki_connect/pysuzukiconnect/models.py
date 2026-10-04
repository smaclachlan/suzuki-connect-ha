"""Typed models and parsers for Suzuki Connect responses.

Field paths are documented in ../docs/API.md and were confirmed against a live
UK e Vitara. Parsing is defensive: the API omits fields depending on vehicle
state (e.g. ``chargerConnected_st`` only appears when plugged in), so every
accessor tolerates missing keys.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from typing import Any, Optional


def _num(value: Any) -> Optional[float]:
    """Parse a number that may arrive as a string with thousands separators."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _int(value: Any) -> Optional[int]:
    n = _num(value)
    return int(n) if n is not None else None


def _bool_yn(value: Any) -> Optional[bool]:
    """Fields like doorLockedRemotely use 'Y'/'N'."""
    if value is None:
        return None
    s = str(value).strip().upper()
    if s in ("Y", "YES", "TRUE", "1"):
        return True
    if s in ("N", "NO", "FALSE", "0"):
        return False
    return None


def _bool_int(value: Any) -> Optional[bool]:
    """Boolean from a numeric 0/non-zero flag; None when the field is absent."""
    i = _int(value)
    return None if i is None else i != 0


def parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse a Suzuki timestamp.

    ``lut`` arrives as a naive local time (``2026-10-02 18:54:37``); those are
    returned naive and the caller attaches the right zone (see ``localize``).
    ISO strings carrying an offset are returned timezone-aware as-is.
    """
    if value in (None, ""):
        return None
    text = str(value).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %I:%M %p",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def localize(value: Optional[datetime], tz: tzinfo) -> Optional[datetime]:
    """Attach ``tz`` to a naive local timestamp; leave aware ones untouched.

    Across a DST change: an ambiguous wall time (the repeated hour in autumn)
    resolves to its first occurrence (fold=0), and a non-existent one (the
    skipped hour in spring) is interpreted with the pre-transition offset, which
    is what zoneinfo does for fold=0. Either way the result converts to a
    single, well-defined UTC instant.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=tz)


def _gps(block: Any) -> Optional[tuple[float, float]]:
    """GPS fields arrive as a 1-item list of {latitude, longitude}."""
    if isinstance(block, list) and block:
        block = block[0]
    if isinstance(block, dict):
        lat = _num(block.get("latitude"))
        lon = _num(block.get("longitude"))
        if lat is not None and lon is not None:
            return (lat, lon)
    return None


@dataclass
class Vehicle:
    """Identity of a vehicle on the account (from the vehicle list)."""

    contract_id: int
    # Identifying/location fields are kept out of repr() so a logged model
    # can't leak them.
    vin: Optional[str] = field(default=None, repr=False)
    generation: Optional[str] = None     # VIN_GEN, e.g. "G3" — a platform code, not a VIN
    brand: Optional[str] = None          # e.g. "e VITARA"
    fuel_type: Optional[str] = None      # e.g. "EV"
    model_code: Optional[str] = None
    odometer: Optional[float] = None
    odometer_unit: Optional[str] = None
    dcm_status: Optional[str] = None     # "ACTIVATED" when telematics is live
    country: Optional[str] = None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def is_ev(self) -> bool:
        return (self.fuel_type or "").upper() == "EV"

    @classmethod
    def from_entry(cls, e: dict) -> "Vehicle":
        return cls(
            contract_id=_int(e.get("CONTRACT_ID")) or 0,
            vin=e.get("VIN"),
            generation=e.get("VIN_GEN"),
            brand=e.get("BrandCode"),
            fuel_type=e.get("FUEL_TYPE"),
            model_code=e.get("VEHICLE_MODEL_CODE"),
            odometer=_num(e.get("ODOMETER_READING")),
            odometer_unit=e.get("ODOMETER_READING_UNIT"),
            dcm_status=e.get("DCM_STATUS"),
            country=e.get("COUNTRY_ID"),
            raw=e,
        )


@dataclass
class VehicleStatus:
    """Live EV/body state from the dashboard's user_data block."""

    state_of_charge: Optional[int] = None      # %
    range: Optional[float] = None
    range_unit: Optional[str] = None
    is_charging: Optional[bool] = None
    charge_status_raw: Optional[int] = None
    charger_connected: Optional[bool] = None
    remaining_charge_minutes: Optional[int] = None
    battery_preconditioning: Optional[bool] = None
    ac_on: Optional[bool] = None
    defogger_on: Optional[bool] = None
    defroster_on: Optional[bool] = None
    seat_heater_on: Optional[bool] = None
    steering_heater_on: Optional[bool] = None
    doors_locked: Optional[bool] = None
    ignition_on: Optional[bool] = None
    # Body states. doors_open is inverted like the lock (0 = open); the rest use
    # non-zero = active (confirmed on the vehicle).
    doors_open: Optional[bool] = None
    hazard_on: Optional[bool] = None
    headlights_on: Optional[bool] = None
    handbrake_on: Optional[bool] = None
    seatbelt_on: Optional[bool] = None
    bonnet_open: Optional[bool] = None
    boot_open: Optional[bool] = None
    location: Optional[tuple[float, float]] = field(default=None, repr=False)
    odometer: Optional[float] = None
    trip_meter: Optional[float] = None   # drv_km; assumed a resettable trip (km)
    vehicle_speed: Optional[float] = None
    average_consumption: Optional[float] = None
    average_consumption_unit: Optional[str] = None
    last_updated: Optional[datetime] = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dashboard(cls, dashboard_data: dict) -> "VehicleStatus":
        """Parse ``result.data.DASHBOARD_DATA`` into a status object."""
        ud = dashboard_data.get("user_data", {}) or {}
        # remainingChargingTime is in milliseconds (16800000 matched the app's
        # "4h 40m"); -1 means "not applicable".
        remaining_ms = _num(ud.get("remainingChargingTime"))
        remaining = (
            round(remaining_ms / 60000)
            if remaining_ms is not None and remaining_ms >= 0 else None
        )

        last_updated = parse_timestamp(dashboard_data.get("lut"))

        charger_connected = _bool_yn(ud.get("chargerConnected_st")) \
            if "chargerConnected_st" in ud else None

        return cls(
            state_of_charge=_int(ud.get("currentChargeLevel")),
            range=_num(ud.get("driving_range")),
            range_unit=ud.get("driving_range_unit"),
            charge_status_raw=_int(ud.get("charge_st")),
            is_charging=_bool_int(ud.get("charge_st")),
            charger_connected=charger_connected,
            remaining_charge_minutes=remaining,
            battery_preconditioning=_bool_int(ud.get("batteryPreconditioning_st")),
            ac_on=_bool_int(ud.get("acOn_st")),
            defogger_on=_bool_int(ud.get("defoggerOn_st")),
            defroster_on=_bool_int(ud.get("defrosterOn_st")),
            seat_heater_on=_bool_int(ud.get("seatHeaterOn_st")),
            steering_heater_on=_bool_int(ud.get("steeringHeaterOn_st")),
            # doorlock_st: 0 = locked, non-zero = unlocked (confirmed on vehicle);
            # absent -> None (unknown)
            doors_locked=(
                None if _int(ud.get("doorlock_st")) is None
                else _int(ud.get("doorlock_st")) == 0
            ),
            ignition_on=_bool_int(ud.get("ignition_status")),
            # opendoor_st is inverted (0 = open), like doorlock_st:
            doors_open=(
                None if _int(ud.get("opendoor_st")) is None
                else _int(ud.get("opendoor_st")) == 0
            ),
            hazard_on=_bool_int(ud.get("hzrd_st")),
            headlights_on=_bool_int(ud.get("headlight_st")),
            handbrake_on=_bool_int(ud.get("prbrk_st")),
            seatbelt_on=_bool_int(ud.get("seatBelt_st")),
            bonnet_open=_bool_int(ud.get("hoodStatus")),
            boot_open=_bool_int(ud.get("trunkStatus")),
            location=_gps(ud.get("latestGPS") or ud.get("GPS")),
            odometer=_num(ud.get("mileage")),
            trip_meter=_num(ud.get("drv_km")),
            vehicle_speed=_num(ud.get("vehicleSpeed")),
            average_consumption=_num(ud.get("averageConsumption")),
            average_consumption_unit=ud.get("averageConsumptionUnit"),
            last_updated=last_updated,
            raw=ud,
        )


@dataclass
class VehicleHealth:
    """Vehicle health summary (from the vehicleHealthStatus endpoint).

    Shape from the decompiled HealthCheckResponse:
    ``result.data.VEHICLE_HEALTH_STATUS[0].{healthStatus, ResultCode,
    drivableAdvice, lastUpdatedTime, failureItems}``. Value semantics (e.g. what
    each healthStatus code means) are not yet verified against a live response.
    """

    status: Optional[str] = None
    drivable_advice: Optional[str] = None
    failure_count: Optional[int] = None
    last_updated: Optional[str] = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_response(cls, payload: dict) -> "VehicleHealth":
        data: dict = {}
        result = payload.get("result") if isinstance(payload, dict) else None
        if isinstance(result, dict):
            data = result.get("data", {}) or {}
        items = data.get("VEHICLE_HEALTH_STATUS")
        item = items[0] if isinstance(items, list) and items else {}
        if not isinstance(item, dict):
            item = {}
        status = item.get("healthStatus")
        if status in (None, ""):
            status = item.get("ResultCode")
        failures = item.get("failureItems")
        return cls(
            status=str(status) if status not in (None, "") else None,
            drivable_advice=item.get("drivableAdvice") or None,
            failure_count=len(failures) if isinstance(failures, list) else None,
            last_updated=item.get("lastUpdatedTime") or None,
            raw=data,
        )


# -- extended data ---------------------------------------------------------
#
# Driving history, charging history, schedules and subscription. Field names
# come from the app's decompiled Gson models (see ../docs/API.md) and have not
# yet been checked against live responses, so value formats (dates, durations,
# units) are parsed leniently and anything unrecognised becomes None. Location
# fields and driver names are deliberately not parsed.


def _result_data(payload: Any) -> dict:
    result = payload.get("result") if isinstance(payload, dict) else None
    data = result.get("data") if isinstance(result, dict) else None
    return data if isinstance(data, dict) else {}


def _dicts(value: Any) -> list[dict]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _flag(value: Any) -> Optional[bool]:
    """A boolean that may arrive as bool, 0/1, "0"/"1", "Y"/"N" or "true"."""
    if isinstance(value, bool):
        return value
    return _bool_yn(value)


def _str(value: Any) -> Optional[str]:
    return (str(value).strip() or None) if value is not None else None


def parse_duration_minutes(value: Any) -> Optional[float]:
    """Minutes from "H:MM", "H:MM:SS", "1h 20m"/"20 min", or a bare number
    (taken as minutes)."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    if ":" in text:
        parts = text.split(":")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return None
        if len(nums) == 2:
            return nums[0] * 60 + nums[1]
        if len(nums) == 3:
            return nums[0] * 60 + nums[1] + nums[2] / 60
        return None
    hours = re.search(r"(\d+(?:\.\d+)?)\s*h", text)
    mins = re.search(r"(\d+(?:\.\d+)?)\s*m", text)
    if hours or mins:
        return (float(hours.group(1)) * 60 if hours else 0.0) + (
            float(mins.group(1)) if mins else 0.0
        )
    return _num(text)


def _join_datetime(date: Any, time: Any) -> Optional[datetime]:
    """Combine separate date and time fields (the time may also be a full
    timestamp on its own). A date without a time gives None rather than a
    misleading midnight."""
    if time in (None, ""):
        return None
    full = parse_timestamp(time)  # a bare "HH:MM" never parses on its own
    if full is not None:
        return full
    if date in (None, ""):
        return None
    return parse_timestamp(f"{str(date).strip()} {str(time).strip()}")


@dataclass
class Trip:
    """One trip from the driving history. Positions are not kept."""

    contract_id: Optional[int] = None
    start: Optional[datetime] = None          # naive local time
    end: Optional[datetime] = None
    distance: Optional[float] = None
    distance_unit: Optional[str] = None
    duration_minutes: Optional[float] = None
    average_consumption: Optional[float] = None
    average_consumption_unit: Optional[str] = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_entry(cls, e: dict, trip_date: Any = None) -> "Trip":
        return cls(
            contract_id=_int(e.get("contractID")),
            start=_join_datetime(e.get("startDate") or trip_date, e.get("startTime")),
            end=_join_datetime(e.get("endDate") or trip_date, e.get("endTime")),
            distance=_num(e.get("tripDistance")),
            distance_unit=_str(e.get("tripDistanceUnit")),
            duration_minutes=parse_duration_minutes(e.get("trip_duration")),
            average_consumption=_num(e.get("avgConsumption")),
            average_consumption_unit=_str(e.get("avgConsumptionUnit")),
            raw=e,
        )


@dataclass
class DrivingHistory:
    """A month of trips (``GET /api/trip/drivingHistory/{yyyy-MM}``).

    The endpoint is per account, not per vehicle; use ``for_contract``.
    """

    trips: list[Trip] = field(default_factory=list)   # newest first
    driving_score: Optional[float] = None
    harsh_acceleration_count: Optional[int] = None
    harsh_braking_count: Optional[int] = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_response(cls, payload: Any) -> "DrivingHistory":
        data = _result_data(payload)
        trips = [
            Trip.from_entry(t, day.get("tripDate"))
            for day in _dicts(data.get("tripDetails"))
            for t in _dicts(day.get("tripList"))
        ]
        report = data.get("driverReport")
        report = report if isinstance(report, dict) else {}
        return cls(
            trips=_newest_first(trips),
            driving_score=_num(report.get("drb_score")),
            harsh_acceleration_count=_int(report.get("harsh_acc_count")),
            harsh_braking_count=_int(report.get("harsh_break_count")),
            raw=data,
        )

    def for_contract(self, contract_id: int, *, only_vehicle: bool) -> list[Trip]:
        """Trips for one vehicle. Trips without a contract id are only
        attributed when the account has a single selected vehicle."""
        return [
            t for t in self.trips
            if t.contract_id == contract_id or (t.contract_id is None and only_vehicle)
        ]

    def merged(self, older: "DrivingHistory") -> "DrivingHistory":
        """This month plus an earlier one (trips only; report stays this month's)."""
        return DrivingHistory(
            trips=_newest_first(self.trips + older.trips),
            driving_score=self.driving_score,
            harsh_acceleration_count=self.harsh_acceleration_count,
            harsh_braking_count=self.harsh_braking_count,
            raw=self.raw,
        )


def _newest_first(trips: list[Trip]) -> list[Trip]:
    return sorted(trips, key=lambda t: t.end or t.start or datetime.min, reverse=True)


@dataclass
class ChargeSession:
    """One entry of the charging history. The charging location is not kept."""

    time: Optional[datetime] = None
    duration_minutes: Optional[float] = None
    start_level: Optional[int] = None          # %
    end_level: Optional[int] = None            # %
    energy: Optional[float] = None             # unit not reported
    charge_type: Optional[str] = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_entry(cls, e: dict) -> "ChargeSession":
        return cls(
            time=parse_timestamp(e.get("chargeTime")),
            duration_minutes=parse_duration_minutes(e.get("batteryChargedDuration")),
            start_level=_int(e.get("batteryLevelAtStartCharge")),
            end_level=_int(e.get("batteryLevelAtStopCharge")),
            energy=_num(e.get("energyConsumption")),
            charge_type=_str(e.get("chargeType")),
            raw=e,
        )


@dataclass
class ChargingHistory:
    """``POST /api/v2/remoteCharge/charging_history``."""

    sessions: list[ChargeSession] = field(default_factory=list)  # newest first
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_response(cls, payload: Any) -> "ChargingHistory":
        data = _result_data(payload)
        sessions = [ChargeSession.from_entry(e) for e in _dicts(data.get("chargingHistoryList"))]
        # Keep the API's order when times don't parse; otherwise newest first.
        if all(s.time is not None for s in sessions):
            sessions.sort(key=lambda s: s.time, reverse=True)
        return cls(sessions=sessions, raw=data)


@dataclass
class ChargeSchedule:
    schedule_id: Optional[str] = None
    active: Optional[bool] = None
    ongoing: Optional[bool] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    days: Any = None
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class ClimateSchedule:
    schedule_id: Optional[str] = None
    active: Optional[bool] = None
    time: Optional[str] = None
    date: Optional[str] = None
    days: Optional[str] = None
    repeat: Optional[bool] = None
    duration: Optional[str] = None
    temperature: Optional[str] = None
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class Schedules:
    """Charge schedules (``POST /api/v2/remoteCharge/getAllSchedules``) or
    climate schedules (``GET /api/v2/climate_control_schedule/getAll/{id}``)."""

    items: list = field(default_factory=list)
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def any_active(self) -> Optional[bool]:
        flags = [i.active for i in self.items if i.active is not None]
        if not self.items:
            return False
        return any(flags) if flags else None

    @classmethod
    def charge_from_response(cls, payload: Any) -> "Schedules":
        data = _result_data(payload)
        return cls(
            items=[
                ChargeSchedule(
                    schedule_id=_str(e.get("scheduleId")),
                    active=_flag(e.get("isActive")),
                    ongoing=_flag(e.get("isOngoing")),
                    start_time=_str(e.get("StartTime")),
                    end_time=_str(e.get("endTime")),
                    days=e.get("notifyDays") or e.get("activeDay"),
                    raw=e,
                )
                for e in _dicts(data.get("schedule"))
            ],
            raw=data,
        )

    @classmethod
    def climate_from_response(cls, payload: Any) -> "Schedules":
        data = _result_data(payload)
        return cls(
            items=[
                ClimateSchedule(
                    schedule_id=_str(e.get("reservation_id")),
                    active=_flag(e.get("active")),
                    time=_str(e.get("schedule_time")),
                    date=_str(e.get("schedule_date")),
                    days=_str(e.get("selected_days") or e.get("activeDay")),
                    repeat=_flag(e.get("isRepeatSelected")),
                    duration=_str(e.get("duration")),
                    temperature=_str(e.get("temperature")),
                    raw=e,
                )
                for e in _dicts(data.get("schedules"))
            ],
            raw=data,
        )


@dataclass
class Subscription:
    """``GET /api/subscription/getStatus/{contractId}``."""

    plan_name: Optional[str] = None
    plan_id: Optional[str] = None
    status: Optional[int] = None   # code meanings unverified
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_response(cls, payload: Any) -> "Subscription":
        data = _result_data(payload)
        details = data.get("subscriptionDetails")
        details = details if isinstance(details, dict) else {}
        return cls(
            plan_name=_str(details.get("planName")),
            plan_id=_str(details.get("planId")),
            status=_int(details.get("subscriptionStatus")),
            raw=data,
        )
