"""Parser tests against sanitized copies of real response shapes."""
import json
from pathlib import Path

from pysuzukiconnect.models import Vehicle, VehicleStatus

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIXTURES / name).read_text())


def _data(payload):
    return payload["result"]["data"]


def test_parse_vehicles():
    vdata = _data(_load("vehicles.json"))["VEHICLE_DATA"]
    entries = (vdata.get("PRIMARY_VEHICLE_LIST") or []) + \
              (vdata.get("SECONDARY_VEHICLE_LIST") or [])
    vehicles = [Vehicle.from_entry(e) for e in entries]
    assert len(vehicles) == 1
    v = vehicles[0]
    assert v.contract_id == 999999
    assert v.brand == "e VITARA"
    assert v.vin == "VF1TEST0000000000"
    assert v.generation == "G3"       # VIN_GEN is a platform code, not the VIN
    assert v.is_ev
    assert v.odometer == 14062.9  # thousands separator parsed
    assert v.odometer_unit == "miles"
    assert v.dcm_status == "ACTIVATED"


def test_parse_dashboard_status():
    dashboard = _data(_load("dashboard.json"))["DASHBOARD_DATA"]
    s = VehicleStatus.from_dashboard(dashboard)
    assert s.state_of_charge == 47
    assert s.range == 88.0
    assert s.range_unit == "mile"
    assert s.is_charging is False
    assert s.charge_status_raw == 0
    assert s.remaining_charge_minutes is None  # -1 normalized to None
    assert s.doors_locked is True              # doorlock_st == 0 means locked
    assert s.doors_open is False                # opendoor_st == 0 means open (inverted)
    assert s.ignition_on is False
    assert s.ac_on is False
    assert s.location == (51.5, -0.12)
    assert s.odometer == 22632.0
    assert s.average_consumption == 0.2
    assert s.last_updated is not None
    assert s.last_updated.year == 2026 and s.last_updated.hour == 18


def test_absent_fields_are_unknown_not_false():
    # An empty payload must yield None (unknown), never a misleading False.
    s = VehicleStatus.from_dashboard({})
    assert s.is_charging is None
    assert s.ac_on is None
    assert s.doors_locked is None
    assert s.ignition_on is None
    assert s.state_of_charge is None


def _status(name):
    return VehicleStatus.from_dashboard(_data(_load(name))["DASHBOARD_DATA"])


def test_charging():
    s = _status("dashboard_charging.json")
    assert s.is_charging is True
    assert s.charge_status_raw == 1
    assert s.charger_connected is True
    assert s.remaining_charge_minutes == 95  # arrives as a string
    assert s.state_of_charge == 62
    assert s.range == 121.0


def test_plugged_in_not_charging():
    s = _status("dashboard_plugged_in_not_charging.json")
    assert s.charger_connected is True
    assert s.is_charging is False
    assert s.remaining_charge_minutes is None


def test_disconnected_charger_field_absent_is_unknown():
    # chargerConnected_st is only sent while plugged in; its absence must read
    # as unknown, not as a confident "unplugged".
    s = _status("dashboard.json")
    assert "chargerConnected_st" not in s.raw
    assert s.charger_connected is None
    assert s.is_charging is False


def test_null_values_are_unknown():
    s = _status("dashboard_null_values.json")
    assert s.state_of_charge is None
    assert s.range is None
    assert s.is_charging is None
    assert s.charger_connected is None
    assert s.doors_locked is None
    assert s.doors_open is None
    assert s.ac_on is None
    assert s.ignition_on is None
    assert s.odometer is None
    assert s.remaining_charge_minutes is None
    assert s.average_consumption is None
    assert s.location is None          # [{latitude: null, longitude: null}]
    assert s.last_updated is None
    # Fields that were not nulled still parse.
    assert s.range_unit == "mile"


def test_dormant_vehicle():
    # Asleep / not reporting: user_data is null, only a stale lut remains.
    s = _status("dashboard_dormant.json")
    assert s.state_of_charge is None
    assert s.is_charging is None
    assert s.location is None
    assert s.raw == {}
    assert s.last_updated is not None
    assert (s.last_updated.year, s.last_updated.month, s.last_updated.day) == (2026, 9, 14)


def test_missing_user_data_and_odd_shapes():
    assert VehicleStatus.from_dashboard({"lut": "not a date"}).last_updated is None
    s = VehicleStatus.from_dashboard({"user_data": {"latestGPS": [], "GPS": None}})
    assert s.location is None
    s = VehicleStatus.from_dashboard({"user_data": {"currentChargeLevel": "n/a"}})
    assert s.state_of_charge is None


if __name__ == "__main__":
    test_parse_vehicles()
    test_parse_dashboard_status()
    test_absent_fields_are_unknown_not_false()
    print("all model tests passed")
