"""Integration setup, vehicle selection, token persistence and diagnostics."""
from __future__ import annotations

import json

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.suzuki_connect.const import (
    CONF_CONTRACT_ID,
    CONF_DEVICE_ID,
    DOMAIN,
)
from custom_components.suzuki_connect.coordinator import TOKEN_SAVE_DELAY, storage_key
from custom_components.suzuki_connect.diagnostics import (
    async_get_config_entry_diagnostics,
)
from fake_session import PASSWORD, load_fixture

EMAIL = "owner@example.com"


def _entry(**data) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="e VITARA",
        unique_id=EMAIL,
        data={
            "email": EMAIL,
            "password": PASSWORD,
            CONF_DEVICE_ID: "dev-1",
            CONF_CONTRACT_ID: 999999,
            **data,
        },
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_creates_entities(hass, patch_session):
    entry = _entry()
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.e_vitara_state_of_charge").state == "47"
    age = hass.states.get("sensor.e_vitara_telemetry_age")
    assert age is not None and float(age.state) >= 0


async def test_unknown_contract_id_retries_setup(hass, patch_session):
    entry = _entry(**{CONF_CONTRACT_ID: 123})
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_legacy_entry_without_contract_id_uses_first_ev(hass, patch_session):
    # Entries created before vehicle selection have no contract id.
    data = dict(_entry().data)
    data.pop(CONF_CONTRACT_ID)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=EMAIL, data=data)
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.data.vehicle.contract_id == 999999


async def test_refresh_token_persisted(hass, hass_storage, patch_session, freezer):
    entry = _entry()
    await _setup(hass, entry)
    coordinator = entry.runtime_data
    # The save is debounced.
    freezer.tick(TOKEN_SAVE_DELAY + 1)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass_storage[storage_key(entry.entry_id)]["data"] == {
        "refresh_token": coordinator.client.auth.refresh_token
    }


async def test_restored_refresh_token_means_no_login(hass, hass_storage, patch_session):
    backend = patch_session
    backend.valid_refresh.add("saved-refresh")
    entry = _entry()
    hass_storage[storage_key(entry.entry_id)] = {
        "version": 1,
        "key": storage_key(entry.entry_id),
        "data": {"refresh_token": "saved-refresh"},
    }
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert backend.logins == []      # the owner's phone was not logged out
    assert backend.refreshes == 1


async def test_remove_entry_deletes_token_store(hass, hass_storage, patch_session):
    entry = _entry()
    await _setup(hass, entry)
    await entry.runtime_data._store.async_save({"refresh_token": "x"})
    assert storage_key(entry.entry_id) in hass_storage
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert storage_key(entry.entry_id) not in hass_storage


async def test_diagnostics_are_redacted(hass, patch_session):
    entry = _entry()
    await _setup(hass, entry)
    coordinator = entry.runtime_data
    diag = await async_get_config_entry_diagnostics(hass, entry)
    dumped = json.dumps(diag, default=str)

    vehicle = load_fixture("vehicles.json")["result"]["data"]["VEHICLE_DATA"][
        "SECONDARY_VEHICLE_LIST"][0]
    for secret in (
        EMAIL, PASSWORD, "dev-1", vehicle["VIN"], "999999", "51.5", "-0.12",
        coordinator.client.auth.access_token, coordinator.client.auth.refresh_token,
    ):
        assert secret not in dumped, secret

    # Useful, non-sensitive information is still there.
    assert diag["status"]["currentChargeLevel"] == 47
    assert diag["status"]["driving_range"] == 88
    assert diag["polling"]["last_update_success"] is True
    assert diag["polling"]["last_latency_s"] is not None
    assert diag["auth"]["has_refresh_token"] is True
    assert diag["telemetry"]["age_s"] is not None
