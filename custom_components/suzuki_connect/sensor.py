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

    # Called with (status, coordinator); most only need the status.
    value_fn: Callable[[Any, Any], Any]
    unit_fn: Callable[[Any], str | None] | None = None


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
        value_fn=lambda s, c: s.state_of_charge,
    ),
    SuzukiSensorDescription(
        key="range",
        translation_key="range",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s, c: s.range,
        unit_fn=_range_unit,
    ),
    SuzukiSensorDescription(
        key="remaining_charge_time",
        translation_key="remaining_charge_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement="min",
        value_fn=lambda s, c: s.remaining_charge_minutes,
    ),
    SuzukiSensorDescription(
        key="odometer",
        translation_key="odometer",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        value_fn=lambda s, c: s.odometer,
    ),
    # Freshness of the telematics data itself (car -> cloud, ~every minute when
    # the car is awake). Distinct from "Last polled" (HA -> cloud).
    SuzukiSensorDescription(
        key="last_updated",
        translation_key="last_reported_by_car",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_registry_enabled_default=True,
        # A TIMESTAMP sensor must be tz-aware or HA marks it unavailable.
        value_fn=lambda s, c: c.vehicle_time(s.last_updated),
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
        value_fn=lambda s, c: (
            round(c.telemetry_age.total_seconds() / 60, 1)
            if c.telemetry_age is not None else None
        ),
    ),
    # --- opt-in diagnostics (disabled by default) ---
    SuzukiSensorDescription(
        key="average_consumption",
        translation_key="average_consumption",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s, c: s.average_consumption,
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
        value_fn=lambda s, c: s.vehicle_speed,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SuzukiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        SuzukiConnectSensor(coordinator, description) for description in SENSORS
    ]
    entities.append(SuzukiLastPolledSensor(coordinator))
    if entry.options.get(CONF_ENABLE_HEALTH):
        entities.append(SuzukiHealthSensor(coordinator))
    async_add_entities(entities)


class SuzukiConnectSensor(SuzukiConnectEntity, SensorEntity):
    """A single Suzuki Connect sensor."""

    entity_description: SuzukiSensorDescription

    def __init__(self, coordinator, description: SuzukiSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.entity_description.unit_fn:
            return self.entity_description.unit_fn(self._status)
        return self.entity_description.native_unit_of_measurement

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self._status, self.coordinator)


class SuzukiLastPolledSensor(SuzukiConnectEntity, SensorEntity):
    """When Home Assistant last successfully synced with the cloud."""

    _attr_translation_key = "last_polled"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "last_polled")

    @property
    def native_value(self) -> Any:
        return self.coordinator.last_polled


class SuzukiHealthSensor(SuzukiConnectEntity, SensorEntity):
    """Overall vehicle health (opt-in; separate endpoint)."""

    _attr_translation_key = "vehicle_health"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "vehicle_health")

    @property
    def native_value(self) -> Any:
        health = self.coordinator.data.health
        return health.status if health else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        health = self.coordinator.data.health
        if not health:
            return None
        return {
            "drivable_advice": health.drivable_advice,
            "failure_count": health.failure_count,
            "last_updated": health.last_updated,
        }
