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
    assert s.doors_locked is True              # doorlock_st == 1
    assert s.ignition_on is False
    assert s.ac_on is False
    assert s.location == (51.5, -0.12)
    assert s.odometer == 22632.0
    assert s.average_consumption == 0.2
    assert s.last_updated is not None
    assert s.last_updated.year == 2026 and s.last_updated.hour == 18


if __name__ == "__main__":
    test_parse_vehicles()
    test_parse_dashboard_status()
    print("all model tests passed")
