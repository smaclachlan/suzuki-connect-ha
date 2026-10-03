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
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SuzukiConfigEntry
from .entity import SuzukiConnectEntity


@dataclass(frozen=True, kw_only=True)
class SuzukiBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[Any], bool | None]


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
    # doorlock_st: 1 = locked. BinarySensor LOCK is on when UNLOCKED.
    SuzukiBinaryDescription(
        key="doors_locked",
        translation_key="doors_locked",
        device_class=BinarySensorDeviceClass.LOCK,
        value_fn=lambda s: (not s.doors_locked) if s.doors_locked is not None else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SuzukiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        SuzukiConnectBinarySensor(coordinator, d) for d in BINARY_SENSORS
    )


class SuzukiConnectBinarySensor(SuzukiConnectEntity, BinarySensorEntity):
    entity_description: SuzukiBinaryDescription

    def __init__(self, coordinator, description: SuzukiBinaryDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self._status)
