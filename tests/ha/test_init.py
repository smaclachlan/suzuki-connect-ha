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
    CONF_ENABLE_EXTENDED,
    CONF_CONTRACT_IDS,
    CONF_DEVICE_ID,
    CONF_SLOW_INTERVAL_MINUTES,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_SLOW_INTERVAL,
    DOMAIN,
)
from custom_components.suzuki_connect.coordinator import TOKEN_SAVE_DELAY, storage_key
from custom_components.suzuki_connect.pysuzukiconnect import const as api
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
    assert hass.states.get("sensor.e_vitara_battery_level").state == "47"
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
    assert hass.states.get("sensor.e_vitara_battery_level").state == "47"
    assert hass.states.get("sensor.swift_battery_level").state == "62"
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
    assert hass.states.get("sensor.swift_battery_level").state == "62"
    assert hass.states.get("sensor.e_vitara_battery_level").state == "47"

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
    assert hass.states.get("sensor.swift_battery_level").state == "unavailable"
    assert hass.states.get("sensor.swift_battery_range").state == "unavailable"
    assert hass.states.get("binary_sensor.swift_charging").state == "unavailable"
    assert hass.states.get("device_tracker.swift_location").state == "unavailable"
    assert hass.states.get("sensor.e_vitara_battery_level").state == "47"

    backend.dashboards[111111] = swift
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.swift_battery_level").state == "62"


async def test_energy_sensors_follow_capacity_and_target(hass, patch_session):
    entry = _entry()
    await _setup(hass, entry)
    assert hass.states.get("sensor.e_vitara_battery_energy_remaining").state == "unknown"
    assert hass.states.get("number.e_vitara_charge_target").state == "80.0"

    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": "number.e_vitara_battery_capacity", "value": 61},
        blocking=True,
    )
    # SoC 47 %: 61 * 0.47 = 28.7 kWh left; (80 - 47) % of 61 = 20.1 kWh to go.
    assert hass.states.get("sensor.e_vitara_battery_energy_remaining").state == "28.7"
    assert hass.states.get("sensor.e_vitara_battery_energy_to_target").state == "20.1"

    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": "number.e_vitara_charge_target", "value": 40},
        blocking=True,
    )
    assert hass.states.get("sensor.e_vitara_battery_energy_to_target").state == "0.0"


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
    assert hass.states.get("sensor.e_vitara_battery_energy_to_target").state == "21.1"


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


def _extended_routes(backend) -> None:
    """Serve the extended endpoints for 999999 (October 2026 trips only)."""
    def ok(name):
        def fn(call):
            if not backend.authorised(call):
                return 401, {}
            return 200, load_fixture(name)
        return fn
    route = backend.session.route
    route("GET", api.EP_DRIVING_HISTORY.format(month="2026-10"), ok("driving_history.json"))
    route("POST", api.EP_CHARGING_HISTORY, ok("charging_history.json"))
    route("POST", api.EP_CHARGE_SCHEDULES, ok("charge_schedules.json"))
    route("GET", api.EP_CLIMATE_SCHEDULES.format(contract_id=999999),
          ok("climate_schedules.json"))
    route("GET", api.EP_SUBSCRIPTION.format(contract_id=999999), ok("subscription.json"))


def _extended_entry(**options) -> MockConfigEntry:
    entry = _entry()
    return MockConfigEntry(
        domain=DOMAIN, title=entry.title, unique_id=EMAIL, version=1, minor_version=2,
        data=dict(entry.data), options={CONF_ENABLE_EXTENDED: True, **options},
    )


async def test_trip_meter(hass, patch_session):
    await _setup(hass, _entry())
    state = hass.states.get("sensor.e_vitara_trip_meter")
    assert float(state.state) == 153
    assert state.attributes["unit_of_measurement"] == "km"


async def test_extended_entities_only_when_enabled(hass, patch_session):
    await _setup(hass, _entry())
    assert hass.states.get("sensor.e_vitara_trip_last_distance") is None
    assert hass.states.get("binary_sensor.e_vitara_charging_schedule") is None


async def test_extended_data(hass, patch_session, freezer):
    freezer.move_to("2026-10-04 12:00:00+01:00")
    backend = patch_session
    _extended_routes(backend)
    entry = _extended_entry()
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED

    trip = hass.states.get("sensor.e_vitara_trip_last_distance")
    assert trip.state == "1042.5"
    assert trip.attributes["unit_of_measurement"] == "km"
    assert trip.attributes["duration_minutes"] == 72
    assert "latitude" not in json.dumps(dict(trip.attributes))

    month = hass.states.get("sensor.e_vitara_trip_distance_this_month")
    assert month.state == "1060.9"  # 18.4 + 1042.5; the other car's trip excluded
    assert month.attributes["trips"] == 2
    assert month.attributes["driving_score"] == 82

    # Car times are local to HA's timezone (US/Pacific in tests); states are UTC.
    assert hass.states.get("sensor.e_vitara_trip_last_end").state == "2026-10-03T01:52:00+00:00"
    charge = hass.states.get("sensor.e_vitara_charging_last_session")
    assert charge.state == "2026-10-03T02:00:00+00:00"
    assert charge.attributes["end_level"] == 62
    assert hass.states.get("sensor.e_vitara_subscription").state == "Suzuki Connect Plus"

    charge_sched = hass.states.get("binary_sensor.e_vitara_charging_schedule")
    assert charge_sched.state == "on"
    assert charge_sched.attributes["schedules"][0]["start_time"] == "00:30"
    assert hass.states.get("binary_sensor.e_vitara_climate_schedule").state == "off"


async def test_extended_data_is_fetched_rarely(hass, patch_session, freezer):
    freezer.move_to("2026-10-04 12:00:00+01:00")
    backend = patch_session
    _extended_routes(backend)
    entry = _extended_entry()
    await _setup(hass, entry)
    coordinator = entry.runtime_data
    calls = lambda: len(backend.session.calls_to(api.EP_CHARGING_HISTORY))  # noqa: E731
    assert calls() == 1

    await coordinator.async_refresh()
    assert calls() == 1  # within the extended interval: not fetched again
    # Values from the earlier fetch are still there.
    assert hass.states.get("sensor.e_vitara_subscription").state == "Suzuki Connect Plus"

    freezer.tick(DEFAULT_SLOW_INTERVAL)
    await coordinator.async_refresh()
    assert calls() == 2


async def test_extended_failures_do_not_fail_the_poll(hass, patch_session, freezer):
    freezer.move_to("2026-10-04 12:00:00+01:00")
    backend = patch_session
    _extended_routes(backend)
    backend.session.route("POST", api.EP_CHARGING_HISTORY, lambda call: (500, {}))
    entry = _extended_entry()
    await _setup(hass, entry)
    assert entry.runtime_data.last_update_success
    assert hass.states.get("sensor.e_vitara_charging_last_session").state == "unknown"
    assert hass.states.get("sensor.e_vitara_trip_last_distance").state == "1042.5"


async def test_extended_diagnostics_are_redacted(hass, patch_session, freezer):
    freezer.move_to("2026-10-04 12:00:00+01:00")
    _extended_routes(patch_session)
    entry = _extended_entry()
    await _setup(hass, entry)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    extended = diag["vehicles"][0]["extended"]
    assert extended["trips_parsed"] == 2
    dumped = json.dumps(extended, default=str)
    for secret in ("51.5", "-0.12", "Test Driver", "999999", "Services"):
        assert secret not in dumped, secret
    assert "tripDistance" in dumped  # shapes are still visible for mapping


async def test_poll_intervals_from_options(hass, patch_session, freezer):
    freezer.move_to("2026-10-04 12:00:00+01:00")
    _extended_routes(patch_session)
    entry = _extended_entry(
        **{CONF_SCAN_INTERVAL_MINUTES: 1, CONF_SLOW_INTERVAL_MINUTES: 30}
    )
    await _setup(hass, entry)
    coordinator = entry.runtime_data
    assert coordinator.update_interval.total_seconds() == 60

    calls = lambda: len(patch_session.session.calls_to(api.EP_CHARGING_HISTORY))  # noqa: E731
    assert calls() == 1
    freezer.tick(29 * 60)
    await coordinator.async_refresh()
    assert calls() == 1
    freezer.tick(60)
    await coordinator.async_refresh()
    assert calls() == 2


async def test_vehicle_list_is_on_the_slow_path(hass, patch_session, freezer):
    backend = patch_session
    entry = _entry()
    await _setup(hass, entry)
    coordinator = entry.runtime_data
    vehicle_calls = lambda: len(backend.session.calls_to(api.EP_VEHICLE_DETAILS))  # noqa: E731
    dashboard_calls = lambda: len(backend.session.calls_to(api.EP_DASHBOARD))  # noqa: E731
    assert (vehicle_calls(), dashboard_calls()) == (1, 1)

    for _ in range(3):  # live polls: dashboard only
        freezer.tick(60)
        await coordinator.async_refresh()
    assert (vehicle_calls(), dashboard_calls()) == (1, 4)

    freezer.tick(DEFAULT_SLOW_INTERVAL)
    await coordinator.async_refresh()
    assert vehicle_calls() == 2


async def test_failed_vehicle_list_refresh_uses_cache(hass, patch_session, freezer):
    backend = patch_session
    entry = _entry()
    await _setup(hass, entry)
    coordinator = entry.runtime_data
    backend.session.route("GET", api.EP_VEHICLE_DETAILS, lambda call: (503, {}))
    freezer.tick(DEFAULT_SLOW_INTERVAL)
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert hass.states.get("sensor.e_vitara_battery_level").state == "47"


async def test_token_diagnostics(hass, patch_session):
    backend = patch_session
    entry = _entry()
    await _setup(hass, entry)
    backend.expire_access()
    await entry.runtime_data.async_refresh()
    auth = (await async_get_config_entry_diagnostics(hass, entry))["auth"]
    assert auth["reported_expires_in"] == 240
    assert auth["access_token_expires_in_s"] is None  # used until rejected
    assert auth["access_token_is_jwt_with_exp"] is False
    assert auth["tokens_rejected"] == 1
    assert auth["last_rejected_token_age_s"] is not None


async def test_upgrade_keeps_existing_entity_ids(hass, patch_session):
    # Renaming entities only changes display names: an install from before
    # the rename keeps its entity ids (and so its automations and history).
    entry = _entry()
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", DOMAIN, "999999_state_of_charge",
        suggested_object_id="e_vitara_state_of_charge", config_entry=entry,
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get("sensor.e_vitara_state_of_charge")
    assert state.state == "47"
    assert state.attributes["friendly_name"] == "e VITARA Battery level"
    assert hass.states.get("sensor.e_vitara_battery_level") is None


async def test_entity_sections(hass, patch_session):
    await _setup(hass, _entry())
    registry = er.async_get(hass)
    category = lambda eid: registry.async_get(eid).entity_category  # noqa: E731
    assert category("binary_sensor.e_vitara_doors") is None              # main sensors
    assert category("sensor.e_vitara_last_reported_by_car") is not None  # diagnostic


async def test_diagnostics_show_raw_timestamp_and_zone(hass, patch_session):
    entry = _entry()
    await _setup(hass, entry)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["ha_time_zone"] == hass.config.time_zone
    vehicle = diag["vehicles"][0]
    assert vehicle["telemetry"]["lut_raw"] == "2026-10-02 18:54:37"
    dashboard = vehicle["dashboard"]
    assert dashboard["lut"] == "2026-10-02 18:54:37"
    assert "user_data" not in dashboard
    assert "999999" not in json.dumps(dashboard)  # selectedContractId redacted


async def test_last_reported_by_car_is_utc(hass, patch_session):
    # lut "2026-10-02 18:54:37" is UTC, whatever HA's timezone.
    await _setup(hass, _entry())
    state = hass.states.get("sensor.e_vitara_last_reported_by_car")
    assert state.state == "2026-10-02T18:54:37+00:00"


async def test_diagnostics_redact_live_leaks(hass, patch_session, freezer):
    # Fields seen unredacted in a real download: mobile number, trip
    # longitudes, the trip id and a signed image URL.
    freezer.move_to("2026-10-04 12:00:00+01:00")
    _extended_routes(patch_session)
    entry = _extended_entry()
    await _setup(hass, entry)
    dumped = json.dumps(await async_get_config_entry_diagnostics(hass, entry), default=str)
    for secret in ("7700900123", "12345", "X-Amz-Credential", "t_123456_261002_1747",
                   "-0.2", "-0.12", "51.6", "Services"):
        assert secret not in dumped, secret
