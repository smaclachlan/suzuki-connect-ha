"""The Suzuki Connect integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .cards import async_register_cards
from .const import CONF_CONTRACT_ID, CONF_CONTRACT_IDS, DOMAIN, STORAGE_VERSION
from .coordinator import SuzukiConnectCoordinator, storage_key

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.CALENDAR,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.NUMBER,
]

type SuzukiConfigEntry = ConfigEntry[SuzukiConnectCoordinator]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the bundled dashboard cards (once, not per account)."""
    await async_register_cards(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SuzukiConfigEntry) -> bool:
    """Set up Suzuki Connect from a config entry."""
    coordinator = SuzukiConnectCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SuzukiConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_flush_token()
    return unloaded


async def async_migrate_entry(hass: HomeAssistant, entry: SuzukiConfigEntry) -> bool:
    """1.1 stored one selected contract id; 1.2 stores a list."""
    if entry.version > 1:
        return False  # downgraded from a future version
    if entry.version == 1 and entry.minor_version < 2:
        data = dict(entry.data)
        contract_id = data.pop(CONF_CONTRACT_ID, None)
        if contract_id is not None:
            data[CONF_CONTRACT_IDS] = [contract_id]
        hass.config_entries.async_update_entry(entry, data=data, minor_version=2)
        _LOGGER.debug("Migrated Suzuki Connect entry to 1.2")
    return True


async def async_remove_entry(hass: HomeAssistant, entry: SuzukiConfigEntry) -> None:
    """Delete the persisted refresh token when the entry is removed."""
    await Store(hass, STORAGE_VERSION, storage_key(entry.entry_id)).async_remove()


async def _async_update_listener(hass: HomeAssistant, entry: SuzukiConfigEntry) -> None:
    """Reload when options (e.g. scan interval) change."""
    await hass.config_entries.async_reload(entry.entry_id)
