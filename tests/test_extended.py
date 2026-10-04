"""Extended data: parsers for trips, charging history, schedules and
subscription, and the request formats the client sends."""
from datetime import datetime

import pytest

from fake_session import PASSWORD, load_fixture, make_backend

from pysuzukiconnect import SuzukiConnectClient, const
from pysuzukiconnect.models import (
    ChargingHistory,
    DrivingHistory,
    Schedules,
    Subscription,
    parse_duration_minutes,
)

CONTRACT = 999999


@pytest.mark.parametrize(
    "raw, minutes",
    [
        ("00:26", 26), ("1:12", 72), ("1:02:30", 62.5), ("20", 20), (45, 45),
        ("3h 05m", 185), ("20 min", 20), ("2h", 120), ("", None), (None, None),
        ("soon", None),
    ],
)
def test_parse_duration_minutes(raw, minutes):
    assert parse_duration_minutes(raw) == minutes


def test_driving_history():
    h = DrivingHistory.from_response(load_fixture("driving_history.json"))
    assert len(h.trips) == 3
    latest = h.trips[0]  # newest first
    assert latest.start == datetime(2026, 10, 2, 17, 40)
    assert latest.end == datetime(2026, 10, 2, 18, 52)
    assert latest.distance == 1042.5          # thousands separator
    assert latest.distance_unit == "km"
    assert latest.duration_minutes == 72
    assert latest.average_consumption == 5.4
    assert h.driving_score == 82
    assert h.harsh_acceleration_count == 3
    assert h.harsh_braking_count == 1


def test_driving_history_hides_location_and_driver():
    h = DrivingHistory.from_response(load_fixture("driving_history.json"))
    text = repr(h)
    for secret in ("51.5", "-0.12", "Test Driver"):
        assert secret not in text


def test_trips_are_filtered_by_contract():
    h = DrivingHistory.from_response(load_fixture("driving_history.json"))
    assert len(h.for_contract(CONTRACT, only_vehicle=False)) == 2
    assert len(h.for_contract(111111, only_vehicle=False)) == 1


def test_trips_without_contract_id_need_a_single_vehicle():
    payload = load_fixture("driving_history.json")
    for day in payload["result"]["data"]["tripDetails"]:
        for trip in day["tripList"]:
            trip.pop("contractID")
    h = DrivingHistory.from_response(payload)
    assert len(h.for_contract(CONTRACT, only_vehicle=True)) == 3
    assert h.for_contract(CONTRACT, only_vehicle=False) == []


def test_trip_time_may_be_a_full_timestamp():
    payload = {"result": {"data": {"tripDetails": [{"tripDate": "2026-10-02", "tripList": [
        {"startTime": "2026-10-02 08:05:00", "endTime": "2026-10-02 08:31:00"},
        {"startDate": "2026-10-03"},  # date without a time -> unknown
    ]}]}}}
    first, second = sorted(
        DrivingHistory.from_response(payload).trips, key=lambda t: t.start is None
    )
    assert first.start == datetime(2026, 10, 2, 8, 5)
    assert second.start is None and second.end is None


def test_merged_months_newest_first():
    older = DrivingHistory.from_response(load_fixture("driving_history.json"))
    newer = DrivingHistory.from_response({"result": {"data": {"tripDetails": [
        {"tripDate": "2026-11-01", "tripList": [
            {"contractID": CONTRACT, "startDate": "2026-11-01", "startTime": "10:00"}]}]}}})
    merged = newer.merged(older)
    assert len(merged.trips) == 4
    assert merged.trips[0].start == datetime(2026, 11, 1, 10, 0)


def test_empty_or_malformed_driving_history():
    for payload in ({}, {"result": {"data": {"tripDetails": None}}},
                    {"result": {"data": {"tripDetails": ["x", {"tripList": "y"}]}}}):
        assert DrivingHistory.from_response(payload).trips == []


def test_charging_history_live_formats():
    c = ChargingHistory.from_response(load_fixture("charging_history.json"))
    latest = c.sessions[0]
    assert latest.time == datetime(2026, 10, 2, 19, 0)       # "2026/10/02 19:00"
    assert latest.start_level == 40 and latest.end_level == 62  # "40%"
    assert latest.energy == 13                                # "13 kWh"
    assert latest.duration_minutes == 45                      # "00 h 45 min"
    assert latest.charge_type == "Rapid"
    assert c.sessions[1].duration_minutes == 185
    for secret in ("51.5", "Services", "Home"):
        assert secret not in repr(c)


def test_charging_history_plug_state():
    payload = load_fixture("charging_history.json")
    assert ChargingHistory.from_response(payload).charger_connected is None
    payload["result"]["data"]["chargerConnectedStatus"] = 1
    assert ChargingHistory.from_response(payload).charger_connected is True
    payload["result"]["data"]["chargerConnectedStatus"] = 0
    assert ChargingHistory.from_response(payload).charger_connected is False


def test_charge_schedules():
    s = Schedules.charge_from_response(load_fixture("charge_schedules.json"))
    assert [i.active for i in s.items] == [True, False]
    assert s.any_active is True
    first = s.items[0]
    assert (first.start_time, first.end_time, first.days) == ("00:30", "05:30", [1, 2, 3, 4, 5])


def test_climate_schedules():
    s = Schedules.climate_from_response(load_fixture("climate_schedules.json"))
    assert s.any_active is False
    item = s.items[0]
    assert (item.time, item.days, item.repeat, item.temperature) == (
        "07:45", "1,2,3,4,5", True, "21")


def test_no_schedules_means_not_active():
    assert Schedules.charge_from_response({"result": {"data": {}}}).any_active is False


def test_subscription():
    s = Subscription.from_response(load_fixture("subscription.json"))
    assert (s.plan_name, s.plan_id, s.status) == ("Suzuki Connect Plus", "P1", 1)


def test_subscription_from_vehicle_list():
    entry = load_fixture("vehicles.json")["result"]["data"]["VEHICLE_DATA"][
        "SECONDARY_VEHICLE_LIST"][0]
    s = Subscription.from_details(entry["subscriptionDetails"])
    assert (s.plan_name, s.plan_id, s.status) == ("Suzuki Connect Plus", "evitara2025_a_foc", 1)
    assert Subscription.from_details(None).plan_name is None


@pytest.mark.parametrize(
    "raw, value",
    [("7 kWh", 7), ("43%", 43), ("14,066.6", 14066.6), ("-3.5", -3.5), (12, 12),
     ("N/A", None), ("", None), (None, None), (True, None)],
)
def test_lenient_numbers(raw, value):
    from pysuzukiconnect.models import _num
    assert _num(raw) == value


# -- request formats (from the decompiled app) -------------------------------

@pytest.fixture
def backend():
    be = make_backend()
    ok = lambda name: lambda call: (200, load_fixture(name))  # noqa: E731
    be.session.route("GET", const.EP_DRIVING_HISTORY.format(month="2026-10"),
                     ok("driving_history.json"))
    be.session.route("POST", const.EP_CHARGING_HISTORY, ok("charging_history.json"))
    be.session.route("POST", const.EP_CHARGE_SCHEDULES, ok("charge_schedules.json"))
    be.session.route("GET", const.EP_CLIMATE_SCHEDULES.format(contract_id=CONTRACT),
                     ok("climate_schedules.json"))
    be.session.route("GET", const.EP_SUBSCRIPTION.format(contract_id=CONTRACT),
                     ok("subscription.json"))
    return be


def _client(be):
    return SuzukiConnectClient(be.session, "owner@example.com", PASSWORD, device_id="dev-1")


async def test_request_formats(backend):
    client = _client(backend)
    assert (await client.async_get_driving_history("2026-10")).trips
    assert (await client.async_get_charging_history(CONTRACT)).sessions
    assert (await client.async_get_charge_schedules(CONTRACT)).items
    assert (await client.async_get_climate_schedules(CONTRACT)).items
    assert (await client.async_get_subscription(CONTRACT)).plan_name

    [history] = backend.session.calls_to(const.EP_CHARGING_HISTORY)
    assert history["json"] == {"contractId": CONTRACT, "default": "0"}
    [schedules] = backend.session.calls_to(const.EP_CHARGE_SCHEDULES)
    assert schedules["json"] == {"contractID": CONTRACT}
    for call in backend.session.calls[1:]:  # all but the login are authorised
        assert call["headers"]["Authorization"].startswith("Bearer ")


@pytest.mark.parametrize("month", ["2026-1", "2026/10", "../x", "202610"])
async def test_driving_history_month_is_validated(backend, month):
    with pytest.raises(ValueError):
        await _client(backend).async_get_driving_history(month)
