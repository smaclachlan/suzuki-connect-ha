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
from homeassistant.const import PERCENTAGE, UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SuzukiConfigEntry
from .entity import SuzukiConnectEntity


def _as_local(value):
    """Make the API's naive 'last reported' time timezone-aware.

    The API reports it in the vehicle's local time (which matches the owner's
    Home Assistant timezone), so a TIMESTAMP sensor needs it tz-aware or HA
    rejects it as unavailable.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return value


@dataclass(frozen=True, kw_only=True)
class SuzukiSensorDescription(SensorEntityDescription):
    """Sensor description with a value getter against VehicleStatus."""

    value_fn: Callable[[Any], Any]
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
        value_fn=lambda s: s.state_of_charge,
    ),
    SuzukiSensorDescription(
        key="range",
        translation_key="range",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.range,
        unit_fn=_range_unit,
    ),
    SuzukiSensorDescription(
        key="remaining_charge_time",
        translation_key="remaining_charge_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement="min",
        value_fn=lambda s: s.remaining_charge_minutes,
    ),
    SuzukiSensorDescription(
        key="odometer",
        translation_key="odometer",
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        value_fn=lambda s: s.odometer,
    ),
    SuzukiSensorDescription(
        key="last_updated",
        translation_key="last_updated",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_registry_enabled_default=True,
        value_fn=lambda s: _as_local(s.last_updated),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SuzukiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        SuzukiConnectSensor(coordinator, description) for description in SENSORS
    )


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
        return self.entity_description.value_fn(self._status)
