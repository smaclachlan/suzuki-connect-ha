"""Config flow: sign-in, vehicle selection, errors."""
from __future__ import annotations

from homeassistant import config_entries
import pytest
import voluptuous as vol
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.suzuki_connect.const import (
    CONF_CONTRACT_IDS,
    CONF_ENABLE_EXTENDED,
    CONF_ENABLE_HEALTH,
    CONF_SLOW_INTERVAL_MINUTES,
    CONF_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)
from fake_session import PASSWORD, load_fixture

EMAIL = "owner@example.com"
USER_INPUT = {"email": EMAIL, "password": PASSWORD, "device_name": "HomeAssistant"}


def _two_vehicles() -> dict:
    payload = load_fixture("vehicles.json")
    vdata = payload["result"]["data"]["VEHICLE_DATA"]
    first = vdata["SECONDARY_VEHICLE_LIST"][0]
    vdata["PRIMARY_VEHICLE_LIST"] = [
        {**first, "CONTRACT_ID": 111111, "VIN": "TSMTEST0000001111",
         "BrandCode": "SWIFT", "FUEL_TYPE": "PETROL"},
    ]
    return payload


async def _start(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_single_vehicle_creates_entry(hass, patch_session):
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "e VITARA"
    assert result["data"][CONF_CONTRACT_IDS] == [999999]
    assert result["result"].unique_id == EMAIL
    assert result["result"].minor_version == 2


async def test_multiple_vehicles_asks_which(hass, patch_session):
    backend = patch_session
    payload = _two_vehicles()
    backend.session.route(
        "GET", "/api/profile/vehicleDetailsAuth",
        lambda call: (200, payload) if backend.authorised(call) else (401, {}),
    )
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "vehicle"
    # EVs are preselected; labels show only the VIN's last four characters.
    schema = result["data_schema"].schema
    key = next(iter(schema))
    assert key.default() == ["999999"]
    labels = schema[key].options
    assert labels["111111"] == "SWIFT (VIN …1111)"
    assert all("TSMTEST" not in label for label in labels.values())

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONTRACT_IDS: ["111111"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "SWIFT"
    assert result["data"][CONF_CONTRACT_IDS] == [111111]


async def test_multiple_vehicles_select_all(hass, patch_session):
    backend = patch_session
    payload = _two_vehicles()
    backend.session.route(
        "GET", "/api/profile/vehicleDetailsAuth",
        lambda call: (200, payload) if backend.authorised(call) else (401, {}),
    )
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONTRACT_IDS: []}
    )
    assert result["errors"] == {"base": "no_vehicle_selected"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONTRACT_IDS: ["999999", "111111"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Suzuki Connect"
    assert sorted(result["data"][CONF_CONTRACT_IDS]) == [111111, 999999]


async def test_invalid_auth(hass, patch_session):
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_INPUT, "password": "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_cannot_connect(hass, patch_session):
    patch_session.session.route(
        "POST", "/api/sconnectapp/login/token", lambda call: (503, {})
    )
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["errors"] == {"base": "cannot_connect"}


def _options_input(scan=15, extended=360) -> dict:
    return {
        CONF_SCAN_INTERVAL_MINUTES: scan,
        CONF_ENABLE_HEALTH: False,
        CONF_ENABLE_EXTENDED: True,
        CONF_SLOW_INTERVAL_MINUTES: extended,
    }


async def _options_flow(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=EMAIL, data={}, minor_version=2)
    entry.add_to_hass(hass)
    return await hass.config_entries.options.async_init(entry.entry_id)


async def test_options_defaults(hass):
    result = await _options_flow(hass)
    defaults = {
        str(k): k.default() for k in result["data_schema"].schema
    }
    assert defaults[CONF_SCAN_INTERVAL_MINUTES] == 15
    assert defaults[CONF_SLOW_INTERVAL_MINUTES] == 360


async def test_options_store_ints(hass):
    # Sliders submit floats; the stored options are whole minutes.
    result = await _options_flow(hass)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _options_input(scan=1.0, extended=30.0)
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_SCAN_INTERVAL_MINUTES] == 1
    assert result["data"][CONF_SLOW_INTERVAL_MINUTES] == 30
    assert isinstance(result["data"][CONF_SCAN_INTERVAL_MINUTES], int)


@pytest.mark.parametrize(
    "scan, extended",
    [(0, 360), (241, 360), (15, 29), (15, 1441)],
)
async def test_options_reject_out_of_range(hass, scan, extended):
    result = await _options_flow(hass)
    with pytest.raises(vol.Invalid):
        await hass.config_entries.options.async_configure(
            result["flow_id"], _options_input(scan=scan, extended=extended)
        )
