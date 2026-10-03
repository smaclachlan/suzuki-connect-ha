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

from . import SuzukiConfigEntry
from .const import CONF_ENABLE_HEALTH
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
    u = (status.range_unit or "").lower()
    if u.startswith("mile"):
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
        entity_registry_enabled_default=True,
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
    def contract_id(self) -> int:
        return self._contract_id

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
