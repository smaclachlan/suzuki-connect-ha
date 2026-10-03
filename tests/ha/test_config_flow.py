"""Config flow: sign-in, vehicle selection, errors."""
from __future__ import annotations

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.suzuki_connect.const import CONF_CONTRACT_ID, DOMAIN
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
    assert result["data"][CONF_CONTRACT_ID] == 999999
    assert result["result"].unique_id == EMAIL


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
    # The EV is preselected; labels show only the VIN's last four characters.
    schema = result["data_schema"].schema
    key = next(iter(schema))
    assert key.default() == "999999"
    labels = schema[key].container
    assert labels["111111"] == "SWIFT (VIN …1111)"
    assert all("TSMTEST" not in label for label in labels.values())

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CONTRACT_ID: "111111"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "SWIFT"
    assert result["data"][CONF_CONTRACT_ID] == 111111


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
