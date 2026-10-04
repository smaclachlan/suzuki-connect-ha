"""Binary sensor platform for Suzuki Connect."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SuzukiConfigEntry
from .const import CONF_ENABLE_EXTENDED
from .coordinator import ExtendedData
from .entity import SuzukiConnectEntity


@dataclass(frozen=True, kw_only=True)
class SuzukiBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[Any], bool | None]


def _climate_active(s) -> bool | None:
    """Any heating/cooling running (A/C, defog/defrost, heaters, preconditioning)."""
    flags = (
        s.ac_on, s.defogger_on, s.defroster_on,
        s.seat_heater_on, s.steering_heater_on, s.battery_preconditioning,
    )
    if any(f is True for f in flags):
        return True
    if all(f is None for f in flags):
        return None
    return False


BINARY_SENSORS: tuple[SuzukiBinaryDescription, ...] = (
    SuzukiBinaryDescription(
        key="charging",
        translation_key="charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda s: s.is_charging,
    ),
    SuzukiBinaryDescription(
        key="charger_connected",
        translation_key="charger_connected",
        device_class=BinarySensorDeviceClass.PLUG,
        value_fn=lambda s: s.charger_connected,
    ),
    # Model maps doorlock_st so doors_locked is True when locked;
    # the LOCK binary sensor is "on" when UNLOCKED.
    SuzukiBinaryDescription(
        key="doors_locked",
        translation_key="doors_locked",
        device_class=BinarySensorDeviceClass.LOCK,
        value_fn=lambda s: (not s.doors_locked) if s.doors_locked is not None else None,
    ),
    SuzukiBinaryDescription(
        key="ignition",
        translation_key="ignition",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda s: s.ignition_on,
    ),
    SuzukiBinaryDescription(
        key="climate_active",
        translation_key="climate_active",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=_climate_active,
    ),
    # --- granular climate states: opt-in (disabled by default) ---
    SuzukiBinaryDescription(
        key="air_conditioning",
        translation_key="air_conditioning",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.ac_on,
    ),
    SuzukiBinaryDescription(
        key="battery_preconditioning",
        translation_key="battery_preconditioning",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.battery_preconditioning,
    ),
    SuzukiBinaryDescription(
        key="defogger",
        translation_key="defogger",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.defogger_on,
    ),
    SuzukiBinaryDescription(
        key="defroster",
        translation_key="defroster",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.defroster_on,
    ),
    SuzukiBinaryDescription(
        key="seat_heater",
        translation_key="seat_heater",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.seat_heater_on,
    ),
    SuzukiBinaryDescription(
        key="steering_heater",
        translation_key="steering_heater",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.steering_heater_on,
    ),
    # --- body states (verified on vehicle; opendoor_st is inverted) ---
    # Doors enabled by default (security-relevant); the rest opt-in.
    SuzukiBinaryDescription(
        key="doors_open",
        translation_key="doors_open",
        device_class=BinarySensorDeviceClass.DOOR,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.doors_open,
    ),
    SuzukiBinaryDescription(
        key="hazard",
        translation_key="hazard",
        device_class=BinarySensorDeviceClass.LIGHT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.hazard_on,
    ),
    SuzukiBinaryDescription(
        key="headlights",
        translation_key="headlights",
        device_class=BinarySensorDeviceClass.LIGHT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.headlights_on,
    ),
    SuzukiBinaryDescription(
        key="handbrake",
        translation_key="handbrake",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.handbrake_on,
    ),
    SuzukiBinaryDescription(
        key="seatbelt",
        translation_key="seatbelt",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.seatbelt_on,
    ),
    SuzukiBinaryDescription(
        key="bonnet",
        translation_key="bonnet",
        device_class=BinarySensorDeviceClass.OPENING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.bonnet_open,
    ),
    SuzukiBinaryDescription(
        key="boot",
        translation_key="boot",
        device_class=BinarySensorDeviceClass.OPENING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.boot_open,
    ),
)


@dataclass(frozen=True, kw_only=True)
class SuzukiScheduleDescription(BinarySensorEntityDescription):
    """On when any schedule of this kind is active (opt-in extended data)."""

    attr: str  # ExtendedData field holding the Schedules


def _schedule_attrs(item) -> dict[str, Any]:
    """A schedule's settings, minus the raw payload."""
    return {k: v for k, v in vars(item).items() if k != "raw"}


SCHEDULE_SENSORS: tuple[SuzukiScheduleDescription, ...] = (
    SuzukiScheduleDescription(
        key="charge_schedule",
        translation_key="charge_schedule",
        attr="charge_schedules",
    ),
    SuzukiScheduleDescription(
        key="climate_schedule",
        translation_key="climate_schedule",
        attr="climate_schedules",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SuzukiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [
        SuzukiConnectBinarySensor(coordinator, cid, d)
        for cid in coordinator.vehicles
        for d in BINARY_SENSORS
    ]
    if entry.options.get(CONF_ENABLE_EXTENDED):
        entities.extend(
            SuzukiScheduleBinarySensor(coordinator, cid, d)
            for cid in coordinator.vehicles
            for d in SCHEDULE_SENSORS
        )
    async_add_entities(entities)


class SuzukiConnectBinarySensor(SuzukiConnectEntity, BinarySensorEntity):
    entity_description: SuzukiBinaryDescription

    def __init__(
        self, coordinator, contract_id: int, description: SuzukiBinaryDescription
    ) -> None:
        super().__init__(coordinator, contract_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self._status)


class SuzukiScheduleBinarySensor(SuzukiConnectEntity, BinarySensorEntity):
    entity_description: SuzukiScheduleDescription

    def __init__(
        self, coordinator, contract_id: int, description: SuzukiScheduleDescription
    ) -> None:
        super().__init__(coordinator, contract_id, description.key)
        self.entity_description = description

    @property
    def _schedules(self):
        vdata = self._vehicle_data
        ext: ExtendedData | None = vdata.extended if vdata else None
        return getattr(ext, self.entity_description.attr) if ext else None

    @property
    def is_on(self) -> bool | None:
        schedules = self._schedules
        return schedules.any_active if schedules else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        schedules = self._schedules
        if schedules is None:
            return None
        return {"schedules": [_schedule_attrs(i) for i in schedules.items]}
