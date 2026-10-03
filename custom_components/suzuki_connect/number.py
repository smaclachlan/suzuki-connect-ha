"""Per-vehicle settings the API doesn't provide: usable battery capacity and a
charge target, used by the Energy remaining / Energy to target sensors.

These are Home Assistant-side values only; nothing is sent to the car.
"""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntityDescription,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SuzukiConfigEntry
from .coordinator import VehicleSettings
from .entity import SuzukiConnectEntity

DEFAULT_CHARGE_TARGET = 80.0


@dataclass(frozen=True, kw_only=True)
class SuzukiNumberDescription(NumberEntityDescription):
    attr: str  # VehicleSettings field
    default: float | None = None


NUMBERS: tuple[SuzukiNumberDescription, ...] = (
    # Suzuki doesn't report capacity and e Vitara variants differ, so the user
    # enters the usable kWh for their car.
    SuzukiNumberDescription(
        key="battery_capacity",
        translation_key="battery_capacity",
        device_class=NumberDeviceClass.ENERGY_STORAGE,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        native_min_value=10,
        native_max_value=150,
        native_step=0.1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        attr="battery_capacity",
    ),
    SuzukiNumberDescription(
        key="charge_target",
        translation_key="charge_target",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=10,
        native_max_value=100,
        native_step=1,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        attr="charge_target",
        default=DEFAULT_CHARGE_TARGET,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SuzukiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        SuzukiSettingNumber(coordinator, cid, d)
        for cid in coordinator.vehicles
        for d in NUMBERS
    )


class SuzukiSettingNumber(SuzukiConnectEntity, RestoreNumber):
    """A user-entered per-vehicle value, restored across restarts."""

    entity_description: SuzukiNumberDescription

    def __init__(
        self, coordinator, contract_id: int, description: SuzukiNumberDescription
    ) -> None:
        super().__init__(coordinator, contract_id, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        # A setting, not telemetry: editable even when the cloud is down.
        return True

    @property
    def _settings(self) -> VehicleSettings:
        return self.coordinator.settings_for(self._contract_id)

    @property
    def native_value(self) -> float | None:
        return getattr(self._settings, self.entity_description.attr)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        value = last.native_value if last else None
        if value is None:
            value = self.entity_description.default
        self._apply(value)

    async def async_set_native_value(self, value: float) -> None:
        self._apply(value)

    def _apply(self, value: float | None) -> None:
        setattr(self._settings, self.entity_description.attr, value)
        # Energy sensors derive from these values.
        self.coordinator.async_update_listeners()
