"""Typed models and parsers for Suzuki Connect responses.

Field paths are documented in ../docs/API.md and were confirmed against a live
UK e Vitara. Parsing is defensive: the API omits fields depending on vehicle
state (e.g. ``chargerConnected_st`` only appears when plugged in), so every
accessor tolerates missing keys.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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


def _find(obj: Any, *names: str) -> Any:
    """Depth-first search for the first non-empty value under any of ``names``."""
    want = {n.lower() for n in names}
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if isinstance(k, str) and k.lower() in want and v not in (None, "", []):
                    return v
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


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
    vehicle_speed: Optional[float] = None
    average_consumption: Optional[float] = None
    average_consumption_unit: Optional[str] = None
    last_updated: Optional[datetime] = None
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_dashboard(cls, dashboard_data: dict) -> "VehicleStatus":
        """Parse ``result.data.DASHBOARD_DATA`` into a status object."""
        ud = dashboard_data.get("user_data", {}) or {}
        remaining = _int(ud.get("remainingChargingTime"))
        if remaining is not None and remaining < 0:
            remaining = None  # -1 means "not applicable"

        lut = dashboard_data.get("lut")
        last_updated = None
        if lut:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
                try:
                    last_updated = datetime.strptime(str(lut), fmt)
                    break
                except ValueError:
                    continue

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
            vehicle_speed=_num(ud.get("vehicleSpeed")),
            average_consumption=_num(ud.get("averageConsumption")),
            average_consumption_unit=ud.get("averageConsumptionUnit"),
            last_updated=last_updated,
            raw=ud,
        )


@dataclass
class VehicleHealth:
    """Vehicle health summary (from the vehicleHealthStatus endpoint).

    Field names come from the decompiled HealthCheckResponse; the exact value
    semantics (e.g. the healthStatus code/string) are not yet verified against a
    live response, so this is exposed defensively.
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
        failures = _find(data, "failureItems")
        status = _find(data, "healthStatus", "ResultCode")
        return cls(
            status=str(status) if status is not None else None,
            drivable_advice=_find(data, "drivableAdvice"),
            failure_count=len(failures) if isinstance(failures, list) else None,
            last_updated=_find(data, "lastUpdatedTime"),
            raw=data,
        )
