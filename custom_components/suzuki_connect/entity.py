"""Base entity for Suzuki Connect."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SuzukiConnectCoordinator


class SuzukiConnectEntity(CoordinatorEntity[SuzukiConnectCoordinator]):
    """Common device info / availability for all entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SuzukiConnectCoordinator, key: str) -> None:
        super().__init__(coordinator)
        vehicle = coordinator.data.vehicle
        self._contract_id = vehicle.contract_id
        self._attr_unique_id = f"{vehicle.contract_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(vehicle.contract_id))},
            manufacturer="Suzuki",
            name=vehicle.brand or "Suzuki",
            model=vehicle.brand,
            serial_number=vehicle.vin,  # None unless a real VIN is present
            hw_version=vehicle.generation,
        )

    @property
    def _status(self):
        return self.coordinator.data.status
