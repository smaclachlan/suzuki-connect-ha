"""Parser tests against sanitized copies of real response shapes."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from pysuzukiconnect.models import (
    Vehicle,
    VehicleHealth,
    VehicleStatus,
    localize,
    parse_timestamp,
)

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
    assert s.remaining_charge_minutes == 95  # 5700000 ms, arrives as a string
    assert s.state_of_charge == 62
    assert s.range == 121.0


@pytest.mark.parametrize(
    "raw, minutes",
    [
        (16800000, 280),     # live value; the app showed "4h 40m"
        ("16800000", 280),
        (90000, 2),          # 1.5 min rounds to nearest
        (0, 0),
        (-1, None),          # not applicable
    ],
)
def test_remaining_charge_time_is_milliseconds(raw, minutes):
    s = VehicleStatus.from_dashboard({"user_data": {"remainingChargingTime": raw}})
    assert s.remaining_charge_minutes == minutes


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


# -- timestamps ----------------------------------------------------------------

LONDON = ZoneInfo("Europe/London")


def test_parse_timestamp_formats():
    assert parse_timestamp("2026-10-02 18:54:37") == datetime(2026, 10, 2, 18, 54, 37)
    assert parse_timestamp("2026/10/02 18:54:37") == datetime(2026, 10, 2, 18, 54, 37)
    aware = parse_timestamp("2026-10-02T17:54:37Z")
    assert aware == datetime(2026, 10, 2, 17, 54, 37, tzinfo=timezone.utc)
    assert parse_timestamp("2026-10-02T18:54:37+01:00").utcoffset() == timedelta(hours=1)
    for bad in (None, "", "yesterday", 12345):
        assert parse_timestamp(bad) is None


def test_localize_summer_and_winter():
    summer = localize(datetime(2026, 7, 1, 12, 0), LONDON)
    winter = localize(datetime(2026, 12, 1, 12, 0), LONDON)
    assert summer.astimezone(timezone.utc).hour == 11   # BST, UTC+1
    assert winter.astimezone(timezone.utc).hour == 12   # GMT


def test_localize_ambiguous_autumn_hour():
    # 2026-10-25 01:30 happens twice in London; resolves to the first (BST).
    t = localize(datetime(2026, 10, 25, 1, 30), LONDON)
    assert t.astimezone(timezone.utc) == datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)


def test_localize_nonexistent_spring_hour():
    # 2026-03-29 01:30 doesn't exist in London; must still map to one instant
    # (pre-transition offset, GMT) rather than raising.
    t = localize(datetime(2026, 3, 29, 1, 30), LONDON)
    assert t.astimezone(timezone.utc) == datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc)


def test_localize_keeps_aware_and_none():
    aware = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert localize(aware, LONDON) is aware
    assert localize(None, LONDON) is None


# -- vehicle health ------------------------------------------------------------

def _health(data):
    return VehicleHealth.from_response({"errors": [], "result": {"data": data}})


def test_health_reads_explicit_path():
    h = _health({
        "VRN": "AB12CDE",
        "SUPPORT_DETAILS": {"healthStatus": "decoy", "drivableAdvice": "decoy"},
        "VEHICLE_HEALTH_STATUS": [{
            "healthStatus": 1,
            "ResultCode": "OK",
            "drivableAdvice": "Safe to drive",
            "lastUpdatedTime": "2026-10-02 18:00:00",
            "failureItems": [{"itemCode": "X1", "name": "Tyre pressure"}],
        }],
    })
    assert h.status == "1"
    assert h.drivable_advice == "Safe to drive"
    assert h.failure_count == 1
    assert h.last_updated == "2026-10-02 18:00:00"


def test_health_ignores_same_named_keys_elsewhere():
    # The old recursive search would have found these decoys.
    h = _health({"SUPPORT_DETAILS": {"healthStatus": "decoy", "failureItems": [1, 2]}})
    assert h.status is None
    assert h.failure_count is None


def test_health_falls_back_to_result_code():
    h = _health({"VEHICLE_HEALTH_STATUS": [{"ResultCode": "E01", "failureItems": []}]})
    assert h.status == "E01"
    assert h.failure_count == 0


def test_health_tolerates_odd_shapes():
    for data in ({}, {"VEHICLE_HEALTH_STATUS": []}, {"VEHICLE_HEALTH_STATUS": [None]},
                 {"VEHICLE_HEALTH_STATUS": "?"}):
        assert _health(data).status is None
    assert VehicleHealth.from_response({}).status is None


if __name__ == "__main__":
    test_parse_vehicles()
    test_parse_dashboard_status()
    test_absent_fields_are_unknown_not_false()
    print("all model tests passed")
