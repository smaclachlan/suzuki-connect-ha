"""Integration setup, vehicle selection, token persistence and diagnostics."""
from __future__ import annotations

import json

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from custom_components.suzuki_connect.const import (
    CONF_CONTRACT_ID,
    CONF_CONTRACT_IDS,
    CONF_DEVICE_ID,
    DOMAIN,
)
from custom_components.suzuki_connect.coordinator import TOKEN_SAVE_DELAY, storage_key
from custom_components.suzuki_connect.diagnostics import (
    async_get_config_entry_diagnostics,
)
from fake_session import PASSWORD, load_fixture

EMAIL = "owner@example.com"


def _entry(minor_version: int = 2, **data) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="e VITARA",
        unique_id=EMAIL,
        version=1,
        minor_version=minor_version,
        data={
            "email": EMAIL,
            "password": PASSWORD,
            CONF_DEVICE_ID: "dev-1",
            CONF_CONTRACT_IDS: [999999],
            **data,
        },
    )


def _second_vehicle(backend) -> None:
    """Add a second (petrol) vehicle to the account with its own dashboard."""
    payload = load_fixture("vehicles.json")
    vdata = payload["result"]["data"]["VEHICLE_DATA"]
    first = vdata["SECONDARY_VEHICLE_LIST"][0]
    vdata["PRIMARY_VEHICLE_LIST"] = [
        {**first, "CONTRACT_ID": 111111, "VIN": "TSMTEST0000001111",
         "BrandCode": "SWIFT", "FUEL_TYPE": "PETROL"},
    ]
    dashboards = {999999: load_fixture("dashboard.json"),
                  111111: load_fixture("dashboard_charging.json")}

    def dashboard(call):
        if not backend.authorised(call):
            return 401, {}
        cid = call["json"]["contract_id"]
        if cid not in dashboards:
            return 500, {}
        return 200, dashboards[cid]

    backend.session.route(
        "GET", "/api/profile/vehicleDetailsAuth",
        lambda call: (200, payload) if backend.authorised(call) else (401, {}),
    )
    backend.session.route("POST", "/api/dashboard/dashboardOauth", dashboard)
    backend.dashboards = dashboards


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
    entry = _entry(**{CONF_CONTRACT_IDS: [123]})
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_legacy_entry_without_contract_id_uses_first_ev(hass, patch_session):
    # Entries created before vehicle selection have no contract id.
    data = dict(_entry().data)
    data.pop(CONF_CONTRACT_IDS)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=EMAIL, data=data, minor_version=2)
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert list(entry.runtime_data.data.vehicles) == [999999]


async def test_migrates_single_contract_id_to_list(hass, patch_session):
    data = dict(_entry().data)
    data.pop(CONF_CONTRACT_IDS)
    data[CONF_CONTRACT_ID] = 999999
    entry = MockConfigEntry(domain=DOMAIN, unique_id=EMAIL, data=data, minor_version=1)
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.minor_version == 2
    assert entry.data[CONF_CONTRACT_IDS] == [999999]
    assert CONF_CONTRACT_ID not in entry.data
    # Unique ids are unchanged, so existing entities keep their history.
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, "999999_state_of_charge")


async def test_two_vehicles_one_login_two_devices(hass, patch_session):
    backend = patch_session
    _second_vehicle(backend)
    entry = _entry(**{CONF_CONTRACT_IDS: [999999, 111111]})
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert backend.logins == ["1"]  # one session shared by both cars

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert sorted(d.name for d in devices) == ["SWIFT", "e VITARA"]
    assert hass.states.get("sensor.e_vitara_state_of_charge").state == "47"
    assert hass.states.get("sensor.swift_state_of_charge").state == "62"
    assert hass.states.get("binary_sensor.swift_charging").state == "on"


async def test_one_vehicle_failing_keeps_the_other(hass, patch_session, freezer):
    backend = patch_session
    _second_vehicle(backend)
    entry = _entry(**{CONF_CONTRACT_IDS: [999999, 111111]})
    await _setup(hass, entry)
    coordinator = entry.runtime_data

    del backend.dashboards[111111]  # SWIFT's telematics now erroring
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert "SWIFT (vehicle 1)" in coordinator.last_error
    # Contract ids are redacted from diagnostics, so must not appear here.
    assert "111111" not in coordinator.last_error
    # Last known values are kept for the failing car; the other updates.
    assert hass.states.get("sensor.swift_state_of_charge").state == "62"
    assert hass.states.get("sensor.e_vitara_state_of_charge").state == "47"

    del backend.dashboards[999999]  # now both fail -> the poll fails
    await coordinator.async_refresh()
    assert not coordinator.last_update_success


async def test_vehicle_failing_first_poll_still_gets_entities(hass, patch_session):
    # A car whose status fails on the first poll used to get no entities until
    # the integration was reloaded; now they exist, unavailable, and recover.
    backend = patch_session
    _second_vehicle(backend)
    swift = backend.dashboards.pop(111111)
    entry = _entry(**{CONF_CONTRACT_IDS: [999999, 111111]})
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert sorted(d.name for d in devices) == ["SWIFT", "e VITARA"]
    assert hass.states.get("sensor.swift_state_of_charge").state == "unavailable"
    assert hass.states.get("sensor.swift_range").state == "unavailable"
    assert hass.states.get("binary_sensor.swift_charging").state == "unavailable"
    assert hass.states.get("device_tracker.swift_location").state == "unavailable"
    assert hass.states.get("sensor.e_vitara_state_of_charge").state == "47"

    backend.dashboards[111111] = swift
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.swift_state_of_charge").state == "62"


async def test_energy_sensors_follow_capacity_and_target(hass, patch_session):
    entry = _entry()
    await _setup(hass, entry)
    assert hass.states.get("sensor.e_vitara_energy_remaining").state == "unknown"
    assert hass.states.get("number.e_vitara_charge_target").state == "80.0"

    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": "number.e_vitara_battery_capacity", "value": 61},
        blocking=True,
    )
    # SoC 47 %: 61 * 0.47 = 28.7 kWh left; (80 - 47) % of 61 = 20.1 kWh to go.
    assert hass.states.get("sensor.e_vitara_energy_remaining").state == "28.7"
    assert hass.states.get("sensor.e_vitara_energy_to_charge_target").state == "20.1"

    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": "number.e_vitara_charge_target", "value": 40},
        blocking=True,
    )
    assert hass.states.get("sensor.e_vitara_energy_to_charge_target").state == "0.0"


async def test_settings_restored_after_restart(hass, patch_session):
    def stored(value, unit):
        return {"native_max_value": 150.0, "native_min_value": 10.0,
                "native_step": 1.0, "native_unit_of_measurement": unit,
                "native_value": value}

    mock_restore_cache_with_extra_data(hass, [
        (State("number.e_vitara_battery_capacity", "49.0"), stored(49.0, "kWh")),
        (State("number.e_vitara_charge_target", "90.0"), stored(90.0, "%")),
    ])
    entry = _entry()
    await _setup(hass, entry)
    settings = entry.runtime_data.settings_for(999999)
    assert settings.battery_capacity == 49.0
    assert settings.charge_target == 90.0
    assert hass.states.get("sensor.e_vitara_energy_to_charge_target").state == "21.1"


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


async def test_unload_flushes_pending_token_save(hass, hass_storage, patch_session):
    # Setup logs in, which schedules a debounced save. Unloading before it
    # fires must still persist the token, or a reload reads a stale one.
    entry = _entry()
    await _setup(hass, entry)
    token = entry.runtime_data.client.auth.refresh_token
    assert storage_key(entry.entry_id) not in hass_storage  # still pending
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass_storage[storage_key(entry.entry_id)]["data"] == {"refresh_token": token}


async def test_remove_entry_with_pending_save_leaves_no_token(
    hass, hass_storage, patch_session, freezer
):
    # A save still pending at removal must not recreate the deleted file.
    entry = _entry()
    await _setup(hass, entry)
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    freezer.tick(TOKEN_SAVE_DELAY + 1)
    async_fire_time_changed(hass)
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
    vehicle_diag = diag["vehicles"][0]
    assert vehicle_diag["status"]["currentChargeLevel"] == 47
    assert vehicle_diag["status"]["driving_range"] == 88
    assert vehicle_diag["telemetry"]["age_s"] is not None
    assert diag["polling"]["last_update_success"] is True
    assert diag["polling"]["last_latency_s"] is not None
    assert diag["auth"]["has_refresh_token"] is True
