"""Device tracker (vehicle location) for Suzuki Connect."""
from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SuzukiConfigEntry
from .entity import SuzukiConnectEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SuzukiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([SuzukiConnectDeviceTracker(entry.runtime_data)])


class SuzukiConnectDeviceTracker(SuzukiConnectEntity, TrackerEntity):
    """Reports the vehicle's last known GPS position."""

    _attr_translation_key = "car"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "location")

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        loc = self._status.location
        return loc[0] if loc else None

    @property
    def longitude(self) -> float | None:
        loc = self._status.location
        return loc[1] if loc else None
