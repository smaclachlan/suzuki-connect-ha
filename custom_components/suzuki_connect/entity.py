"""Base entity for Suzuki Connect."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SuzukiConnectCoordinator, VehicleData


class SuzukiConnectEntity(CoordinatorEntity[SuzukiConnectCoordinator]):
    """Common device info / availability for one vehicle's entities."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: SuzukiConnectCoordinator, contract_id: int, key: str
    ) -> None:
        super().__init__(coordinator)
        vehicle = coordinator.vehicles[contract_id]
        self._contract_id = contract_id
        self._attr_unique_id = f"{contract_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(contract_id))},
            manufacturer="Suzuki",
            name=vehicle.brand or "Suzuki",
            model=vehicle.brand,
            serial_number=vehicle.vin,  # None unless a real VIN is present
            hw_version=vehicle.generation,
        )

    @property
    def contract_id(self) -> int:
        return self._contract_id

    @property
    def available(self) -> bool:
        return super().available and self._vehicle_data is not None

    @property
    def _vehicle_data(self) -> VehicleData | None:
        return self.coordinator.data.vehicles.get(self._contract_id)

    @property
    def _status(self):
        """The latest status; None until this car has reported once."""
        vdata = self._vehicle_data
        return vdata.status if vdata else None
