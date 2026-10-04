"""Sensor platform for Suzuki Connect."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfLength,
    UnitOfSpeed,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SuzukiConfigEntry
from .const import CONF_ENABLE_EXTENDED, CONF_ENABLE_HEALTH
from .coordinator import ExtendedData
from .entity import SuzukiConnectEntity


@dataclass(frozen=True, kw_only=True)
class SuzukiSensorDescription(SensorEntityDescription):
    """Sensor description with a value getter against VehicleStatus."""

    # Called with (status, entity); most only need the status.
    value_fn: Callable[[Any, Any], Any]
    unit_fn: Callable[[Any], str | None] | None = None


def _minutes(delta) -> float | None:
    return round(delta.total_seconds() / 60, 1) if delta is not None else None


def _energy(status, entity, *, to_target: bool) -> float | None:
    """kWh from SoC and the user-entered usable capacity (and charge target).

    Unknown until the Battery capacity number has been set: Suzuki doesn't
    report capacity, and e Vitara variants differ.
    """
    settings = entity.coordinator.settings_for(entity.contract_id)
    soc = status.state_of_charge
    if settings.battery_capacity is None or soc is None:
        return None
    if to_target:
        if settings.charge_target is None:
            return None
        percent = max(0.0, settings.charge_target - soc)
    else:
        percent = soc
    return round(settings.battery_capacity * percent / 100, 1)


def _range_unit(status) -> str | None:
    return _distance_unit(status.range_unit)


def _distance_unit(unit: str | None) -> str | None:
    u = (unit or "").strip().lower()
    if u.startswith("mi"):
        return UnitOfLength.MILES
    if u.startswith("km") or u.startswith("kilo"):
        return UnitOfLength.KILOMETERS
    return None


SENSORS: tuple[SuzukiSensorDescription, ...] = (
    SuzukiSensorDescription(
        key="state_of_charge",
        translation_key="state_of_charge",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda s, e: s.state_of_charge,
    ),
    SuzukiSensorDescription(
        key="range",
        translation_key="range",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s, e: s.range,
        unit_fn=_range_unit,
    ),
    SuzukiSensorDescription(
        key="remaining_charge_time",
        translation_key="remaining_charge_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement="min",
        value_fn=lambda s, e: s.remaining_charge_minutes,
    ),
    # drv_km, raw. Not a trip meter after all: live it rises and drops back
    # to ~0 several times per drive, and no unit fits (as 0.01 km/mi it would
    # mean 75 mph on a country road). So no unit, device class or state class
    # (a "total increasing" meter would build bogus statistics from the
    # drops), and hidden until its meaning is known. Key kept for the ID.
    SuzukiSensorDescription(
        key="trip_meter",
        translation_key="trip_meter",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s, e: s.trip_meter,
    ),
    SuzukiSensorDescription(
        key="odometer",
        translation_key="odometer",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        value_fn=lambda s, e: s.odometer,
    ),
    # Freshness of the telematics data itself (car -> cloud, ~every minute when
    # the car is awake). Distinct from "Last polled" (HA -> cloud).
    SuzukiSensorDescription(
        key="last_updated",
        translation_key="last_reported_by_car",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        # A TIMESTAMP sensor must be tz-aware or HA marks it unavailable.
        value_fn=lambda s, e: e.coordinator.vehicle_time(s.last_updated),
    ),
    # How stale the car's data was when HA last polled. Large values mean the
    # car is asleep/out of coverage even though polling is succeeding.
    SuzukiSensorDescription(
        key="telemetry_age",
        translation_key="telemetry_age",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s, e: _minutes(e.coordinator.telemetry_age(e.contract_id)),
    ),
    # Derived from SoC and the per-vehicle Battery capacity / Charge target
    # numbers; unknown until capacity is set.
    SuzukiSensorDescription(
        key="energy_remaining",
        translation_key="energy_remaining",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda s, e: _energy(s, e, to_target=False),
    ),
    SuzukiSensorDescription(
        key="energy_to_target",
        translation_key="energy_to_target",
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        value_fn=lambda s, e: _energy(s, e, to_target=True),
    ),
    # --- opt-in diagnostics (disabled by default) ---
    SuzukiSensorDescription(
        key="average_consumption",
        translation_key="average_consumption",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s, e: s.average_consumption,
        unit_fn=lambda s: s.average_consumption_unit,
    ),
    SuzukiSensorDescription(
        key="vehicle_speed",
        translation_key="vehicle_speed",
        device_class=SensorDeviceClass.SPEED,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s, e: s.vehicle_speed,
    ),
)


@dataclass(frozen=True, kw_only=True)
class SuzukiExtendedSensorDescription(SensorEntityDescription):
    """A sensor over the rarely-refreshed extended data.

    ``value_fn``/``attrs_fn``/``unit_fn`` get (extended data, entity); the
    entity is passed for timezone handling and the current month.
    """

    value_fn: Callable[[ExtendedData, Any], Any]
    attrs_fn: Callable[[ExtendedData, Any], dict[str, Any] | None] | None = None
    unit_fn: Callable[[ExtendedData], str | None] | None = None


def _last_trip(ext: ExtendedData):
    return ext.trips[0] if ext.trips else None


def _iso(entity, value) -> str | None:
    aware = entity.coordinator.vehicle_time(value)
    return aware.isoformat() if aware else None


RECENT_TRIPS = 10  # also the number of recent charging sessions


def _trip_attrs(ext: ExtendedData, entity) -> dict[str, Any] | None:
    trip = _last_trip(ext)
    if trip is None:
        return None
    return {
        "start": _iso(entity, trip.start),
        "end": _iso(entity, trip.end),
        "duration_minutes": trip.duration_minutes,
        "average_consumption": trip.average_consumption,
        "average_consumption_unit": trip.average_consumption_unit,
        # Newest first, for a dashboard table (see README). Not recorded.
        "recent_trips": [
            {
                "start": _iso(entity, t.start),
                "end": _iso(entity, t.end),
                "distance": t.distance,
                "distance_unit": t.distance_unit,
                "duration_minutes": t.duration_minutes,
                "average_consumption": t.average_consumption,
                "average_consumption_unit": t.average_consumption_unit,
                "battery_used_pct": t.battery_used_pct,
            }
            for t in (ext.trips or [])[:RECENT_TRIPS]
        ],
    }


def _month_trips(ext: ExtendedData) -> list | None:
    if ext.trips is None:
        return None
    now = dt_util.now()
    return [
        t for t in ext.trips
        if (when := t.start or t.end) and (when.year, when.month) == (now.year, now.month)
    ]


def _month_distance(ext: ExtendedData) -> float | None:
    trips = _month_trips(ext)
    if trips is None:
        return None
    unit = _month_unit(ext)
    return round(sum(t.distance or 0 for t in trips if t.distance_unit == unit or unit is None), 1)


def _month_unit(ext: ExtendedData) -> str | None:
    trips = _month_trips(ext) or ext.trips or []
    return next((t.distance_unit for t in trips if t.distance_unit), None)


def _month_attrs(ext: ExtendedData, entity) -> dict[str, Any] | None:
    trips = _month_trips(ext)
    if trips is None:
        return None
    report = ext.driving
    return {
        "trips": len(trips),
        # Account-wide figures from the app's monthly driver report.
        "driving_score": report.driving_score if report else None,
        "harsh_acceleration_count": report.harsh_acceleration_count if report else None,
        "harsh_braking_count": report.harsh_braking_count if report else None,
    }


def _last_charge(ext: ExtendedData):
    return ext.charging.sessions[0] if ext.charging and ext.charging.sessions else None


def _charge_attrs(ext: ExtendedData, entity) -> dict[str, Any] | None:
    session = _last_charge(ext)
    if session is None:
        return None
    return {
        "start_level": session.start_level,
        "end_level": session.end_level,
        "duration_minutes": session.duration_minutes,
        "energy": session.energy,  # kWh
        "charge_type": session.charge_type,
        # Newest first, for the charging sessions card. Not recorded.
        "recent_sessions": [
            {
                "start": _iso(entity, s.time),
                "duration_minutes": s.duration_minutes,
                "start_level": s.start_level,
                "end_level": s.end_level,
                "energy": s.energy,
                "charge_type": s.charge_type,
            }
            for s in entity.coordinator.charge_sessions(entity.contract_id)[:RECENT_TRIPS]
        ],
    }


EXTENDED_SENSORS: tuple[SuzukiExtendedSensorDescription, ...] = (
    SuzukiExtendedSensorDescription(
        key="last_trip_distance",
        translation_key="last_trip_distance",
        device_class=SensorDeviceClass.DISTANCE,
        value_fn=lambda x, e: t.distance if (t := _last_trip(x)) else None,
        unit_fn=lambda x: _distance_unit(t.distance_unit) if (t := _last_trip(x)) else None,
        attrs_fn=_trip_attrs,
    ),
    SuzukiExtendedSensorDescription(
        key="last_trip_end",
        translation_key="last_trip_end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda x, e: (
            e.coordinator.vehicle_time(t.end or t.start) if (t := _last_trip(x)) else None
        ),
    ),
    SuzukiExtendedSensorDescription(
        key="distance_this_month",
        translation_key="distance_this_month",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda x, e: _month_distance(x),
        unit_fn=lambda x: _distance_unit(_month_unit(x)),
        attrs_fn=_month_attrs,
    ),
    SuzukiExtendedSensorDescription(
        key="last_charge",
        translation_key="last_charge",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda x, e: (
            e.coordinator.vehicle_time(c.time) if (c := _last_charge(x)) else None
        ),
        attrs_fn=_charge_attrs,
    ),
    SuzukiExtendedSensorDescription(
        key="subscription",
        translation_key="subscription",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda x, e: x.subscription.plan_name if x.subscription else None,
        attrs_fn=lambda x, e: (
            {"status_code": x.subscription.status} if x.subscription else None
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SuzukiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for cid in coordinator.vehicles:
        entities.extend(SuzukiConnectSensor(coordinator, cid, d) for d in SENSORS)
        entities.append(SuzukiLastPolledSensor(coordinator, cid))
        if entry.options.get(CONF_ENABLE_HEALTH):
            entities.append(SuzukiHealthSensor(coordinator, cid))
        if entry.options.get(CONF_ENABLE_EXTENDED):
            entities.extend(
                SuzukiExtendedSensor(coordinator, cid, d) for d in EXTENDED_SENSORS
            )
    async_add_entities(entities)


class SuzukiConnectSensor(SuzukiConnectEntity, SensorEntity):
    """A single Suzuki Connect sensor."""

    entity_description: SuzukiSensorDescription

    def __init__(
        self, coordinator, contract_id: int, description: SuzukiSensorDescription
    ) -> None:
        super().__init__(coordinator, contract_id, description.key)
        self.entity_description = description

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.entity_description.unit_fn:
            # Read even while unavailable, before the car has ever reported.
            status = self._status
            return self.entity_description.unit_fn(status) if status else None
        return self.entity_description.native_unit_of_measurement

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._status, self)


class SuzukiLastPolledSensor(SuzukiConnectEntity, SensorEntity):
    """When Home Assistant last successfully synced with the cloud."""

    _attr_translation_key = "last_polled"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, contract_id: int) -> None:
        super().__init__(coordinator, contract_id, "last_polled")

    @property
    def native_value(self) -> Any:
        return self.coordinator.last_polled


class SuzukiHealthSensor(SuzukiConnectEntity, SensorEntity):
    """Overall vehicle health (opt-in; separate endpoint)."""

    _attr_translation_key = "vehicle_health"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # Suzuki has only ever returned an empty health list (live, e Vitara).
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator, contract_id: int) -> None:
        super().__init__(coordinator, contract_id, "vehicle_health")

    @property
    def native_value(self) -> Any:
        health = self._vehicle_data.health if self._vehicle_data else None
        return health.status if health else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        health = self._vehicle_data.health if self._vehicle_data else None
        if not health:
            return None
        return {
            "drivable_advice": health.drivable_advice,
            "failure_count": health.failure_count,
            "last_updated": health.last_updated,
        }


class SuzukiExtendedSensor(SuzukiConnectEntity, SensorEntity):
    """A sensor over trips, charging history or subscription (opt-in)."""

    entity_description: SuzukiExtendedSensorDescription
    # A list of trips: shown in the UI, but too big to store with every
    # state change in the recorder.
    _unrecorded_attributes = frozenset({"recent_trips", "recent_sessions"})

    def __init__(
        self, coordinator, contract_id: int, description: SuzukiExtendedSensorDescription
    ) -> None:
        super().__init__(coordinator, contract_id, description.key)
        self.entity_description = description

    @property
    def _extended(self) -> ExtendedData | None:
        vdata = self._vehicle_data
        return vdata.extended if vdata else None

    @property
    def native_value(self) -> Any:
        ext = self._extended
        return self.entity_description.value_fn(ext, self) if ext else None

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.entity_description.unit_fn is None:
            return self.entity_description.native_unit_of_measurement
        ext = self._extended
        return self.entity_description.unit_fn(ext) if ext else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        ext = self._extended
        if ext is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(ext, self)
