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
    vin: Optional[str] = None
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
    location: Optional[tuple[float, float]] = None
    odometer: Optional[float] = None
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
            is_charging=(_int(ud.get("charge_st")) or 0) != 0,
            charger_connected=charger_connected,
            remaining_charge_minutes=remaining,
            battery_preconditioning=_bool_yn(ud.get("batteryPreconditioning_st"))
                if str(ud.get("batteryPreconditioning_st")) in ("Y", "N")
                else (_int(ud.get("batteryPreconditioning_st")) or 0) != 0,
            ac_on=(_int(ud.get("acOn_st")) or 0) != 0,
            defogger_on=(_int(ud.get("defoggerOn_st")) or 0) != 0,
            defroster_on=(_int(ud.get("defrosterOn_st")) or 0) != 0,
            seat_heater_on=(_int(ud.get("seatHeaterOn_st")) or 0) != 0,
            steering_heater_on=(_int(ud.get("steeringHeaterOn_st")) or 0) != 0,
            # doorlock_st: 0 = locked, non-zero = unlocked (confirmed on vehicle)
            doors_locked=(_int(ud.get("doorlock_st")) == 0),
            ignition_on=(_int(ud.get("ignition_status")) or 0) != 0,
            location=_gps(ud.get("latestGPS") or ud.get("GPS")),
            odometer=_num(ud.get("mileage")),
            average_consumption=_num(ud.get("averageConsumption")),
            average_consumption_unit=ud.get("averageConsumptionUnit"),
            last_updated=last_updated,
            raw=ud,
        )
