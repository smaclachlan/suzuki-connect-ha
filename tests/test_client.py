"""Client/auth behaviour: HTTP error handling, token lifecycle, concurrency and
recovery when the Suzuki app takes over the single active session."""
import asyncio
import json
from pathlib import Path

import aiohttp
import pytest

from fake_session import FakeSession, envelope, error_envelope

from pysuzukiconnect import (
    SuzukiAnotherActiveLogin,
    SuzukiApiError,
    SuzukiAuthError,
    SuzukiConnectClient,
    SuzukiConnectionError,
    SuzukiSessionTakenOver,
)
from pysuzukiconnect import auth as auth_mod
from pysuzukiconnect import const

FIXTURES = Path(__file__).parent / "fixtures"
EMAIL = "owner@example.com"
PASSWORD = "hunter2-secret"
CONTRACT = 999999


class FakeClock:
    """Replaces the ``time`` module inside auth.py.

    Deliberately has no ``time()``: token expiry must use the monotonic clock,
    so any wall-clock use in auth.py fails these tests.
    """

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(auth_mod, "time", c)
    return c


class FakeBackend:
    """Simulates Suzuki's token endpoint and single-session semantics.

    Each successful login/refresh mints a new token; ``evict()`` simulates the
    phone app logging in with override, invalidating everything we hold.
    """

    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.counter = 0
        self.valid_access: set[str] = set()
        self.valid_refresh: set[str] = set()
        self.logins: list[str] = []  # override flag per password login
        self.refreshes = 0
        self.refresh_survives_eviction = False
        session.route("POST", const.EP_LOGIN, self._login)

    def _mint(self) -> dict:
        self.counter += 1
        access, refresh = f"access-{self.counter}", f"refresh-{self.counter}"
        self.valid_access.add(access)
        self.valid_refresh.add(refresh)
        return envelope(access_token=access, refresh_token=refresh, expiresIn=240)

    def _login(self, call):
        form = call["data"]
        if form["grant_type"] == const.GRANT_REFRESH:
            self.refreshes += 1
            if form["refresh_token"] not in self.valid_refresh:
                return 400, error_envelope(400001, "Invalid refresh token")
            return 200, self._mint()
        self.logins.append(form["override"])
        if form["password"] != PASSWORD:
            return 400, error_envelope(400002, "Invalid credentials")
        self.valid_access.clear()
        self.valid_refresh.clear()
        return 200, self._mint()

    def evict(self) -> None:
        self.valid_access.clear()
        if not self.refresh_survives_eviction:
            self.valid_refresh.clear()

    def authorised(self, call) -> bool:
        auth = call["headers"].get("Authorization", "")
        return auth.removeprefix("Bearer ") in self.valid_access


def _fixture(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def backend():
    session = FakeSession()
    be = FakeBackend(session)

    def data_route(name):
        def fn(call):
            if not be.authorised(call):
                return 401, {"message": "Unauthorized"}
            return 200, _fixture(name)
        return fn

    session.route("GET", const.EP_VEHICLE_DETAILS, data_route("vehicles.json"))
    session.route("POST", const.EP_DASHBOARD, data_route("dashboard.json"))
    return be


def _client(be: FakeBackend, password: str = PASSWORD) -> SuzukiConnectClient:
    return SuzukiConnectClient(be.session, EMAIL, password, device_id="dev-1")


# -- HTTP error handling ------------------------------------------------------

@pytest.mark.parametrize(
    "status, body",
    [
        (500, {"result": {"data": {}}}),        # valid JSON, no errors array
        (500, {"errors": [], "result": {}}),    # empty errors array
        (502, "<html>Bad Gateway</html>"),      # non-JSON proxy page
        (404, ""),                               # empty body
    ],
)
async def test_unsuccessful_http_is_rejected(backend, clock, status, body):
    backend.session.route("POST", const.EP_DASHBOARD, lambda call: (status, body))
    with pytest.raises(SuzukiApiError) as exc:
        await _client(backend).async_get_status(CONTRACT)
    assert exc.value.status == status


async def test_application_error_in_body_wins(backend, clock):
    backend.session.route(
        "POST", const.EP_DASHBOARD,
        lambda call: (200, error_envelope(500123, "Vehicle not reachable")),
    )
    with pytest.raises(SuzukiApiError) as exc:
        await _client(backend).async_get_status(CONTRACT)
    assert exc.value.code == 500123
    assert "Vehicle not reachable" in str(exc.value)


async def test_successful_non_object_body_is_rejected(backend, clock):
    backend.session.route("POST", const.EP_DASHBOARD, lambda call: (200, ["?"]))
    with pytest.raises(SuzukiApiError):
        await _client(backend).async_get_status(CONTRACT)


async def test_transport_error_becomes_connection_error(backend, clock):
    def boom(call):
        raise aiohttp.ClientConnectionError("connection reset")

    backend.session.route("POST", const.EP_DASHBOARD, boom)
    with pytest.raises(SuzukiConnectionError):
        await _client(backend).async_get_status(CONTRACT)


async def test_timeout_becomes_connection_error(backend, clock):
    def slow(call):
        raise asyncio.TimeoutError

    backend.session.route("GET", const.EP_VEHICLE_DETAILS, slow)
    with pytest.raises(SuzukiConnectionError):
        await _client(backend).async_get_vehicles()


async def test_login_another_active_login_over_http_505(backend, clock):
    # Suzuki's unusual response: application error 400008 delivered as HTTP 505.
    backend.session.route(
        "POST", const.EP_LOGIN,
        lambda call: (505, error_envelope(
            const.ERR_ANOTHER_ACTIVE_LOGIN, "Another Active Login", "Pixel 8")),
    )
    with pytest.raises(SuzukiAnotherActiveLogin) as exc:
        await _client(backend).auth.async_login()
    assert exc.value.other_device == "Pixel 8"


async def test_login_server_error_is_not_an_auth_error(backend, clock):
    # A 5xx must not look like bad credentials (that would start a reauth flow).
    backend.session.route("POST", const.EP_LOGIN, lambda call: (503, {"result": {}}))
    with pytest.raises(SuzukiApiError):
        await _client(backend).async_get_vehicles()


async def test_login_http_401_without_body_errors_is_auth_error(backend, clock):
    backend.session.route("POST", const.EP_LOGIN, lambda call: (401, {}))
    with pytest.raises(SuzukiAuthError):
        await _client(backend).async_get_vehicles()


async def test_bad_password_is_auth_error(backend, clock):
    with pytest.raises(SuzukiAuthError):
        await _client(backend, password="wrong").async_get_vehicles()


# -- token lifecycle ------------------------------------------------------------

async def test_token_reused_until_expiry_then_refreshed(backend, clock):
    client = _client(backend)
    await client.async_get_vehicles()
    await client.async_get_vehicles()
    assert backend.logins == ["1"] and backend.refreshes == 0

    clock.now += 240  # past expiresIn (minus the 30 s margin)
    await client.async_get_vehicles()
    assert backend.logins == ["1"] and backend.refreshes == 1


async def test_concurrent_requests_share_one_login(backend, clock):
    client = _client(backend)
    await asyncio.gather(*(client.async_get_vehicles() for _ in range(5)))
    assert backend.logins == ["1"]


async def test_concurrent_refresh_happens_once(backend, clock):
    client = _client(backend)
    await client.async_get_vehicles()
    clock.now += 240
    await asyncio.gather(*(client.async_get_status(CONTRACT) for _ in range(5)))
    assert backend.refreshes == 1 and backend.logins == ["1"]


async def test_concurrent_401s_reauth_once(backend, clock):
    # Several in-flight requests all hit 401 with the same stale token; only
    # one of them should re-authenticate, and none may discard the new token.
    client = _client(backend)
    await client.async_get_vehicles()
    backend.refresh_survives_eviction = True
    backend.evict()
    await asyncio.gather(*(client.async_get_status(CONTRACT) for _ in range(5)))
    assert backend.refreshes == 1 and backend.logins == ["1"]


# -- single active session ------------------------------------------------------

async def test_eviction_recovers_via_refresh_without_forced_login(backend, clock):
    client = _client(backend)
    await client.async_get_vehicles()
    backend.refresh_survives_eviction = True
    backend.evict()  # phone app logs in
    status = await client.async_get_status(CONTRACT)
    assert status.state_of_charge == 47
    assert backend.logins == ["1"]  # the phone was not kicked out


async def test_eviction_recovers_via_one_forced_login(backend, clock):
    client = _client(backend)
    await client.async_get_vehicles()
    clock.now += const.FORCED_LOGIN_COOLDOWN
    backend.evict()  # phone app logs in; our refresh token dies with it
    status = await client.async_get_status(CONTRACT)
    assert status.state_of_charge == 47
    assert backend.logins == ["1", "1"]


async def test_phone_reclaiming_repeatedly_does_not_ping_pong(backend, clock):
    """If the phone keeps taking the session back, HA reclaims it at most once
    per cooldown and otherwise fails the poll (not a reauth)."""
    client = _client(backend)
    await client.async_get_vehicles()
    clock.now += const.FORCED_LOGIN_COOLDOWN

    # The phone immediately re-takes the session after every login of ours.
    real_login = backend._login

    def login_then_phone_evicts(call):
        result = real_login(call)
        backend.evict()
        return result

    backend.session.route("POST", const.EP_LOGIN, login_then_phone_evicts)
    backend.evict()

    # Poll 1: one forced login; its retry is rejected again and the cooldown
    # stops a second login -> the poll fails without kicking the phone twice.
    with pytest.raises(SuzukiSessionTakenOver):
        await client.async_get_status(CONTRACT)
    assert backend.logins == ["1", "1"]

    # Further polls inside the cooldown don't log in again.
    for _ in range(3):
        clock.now += 60
        with pytest.raises(SuzukiSessionTakenOver):
            await client.async_get_status(CONTRACT)
    assert backend.logins == ["1", "1"]
    assert not isinstance(SuzukiSessionTakenOver(1), SuzukiAuthError)

    # Once the cooldown has passed, HA may reclaim again.
    clock.now += const.FORCED_LOGIN_COOLDOWN
    with pytest.raises(SuzukiSessionTakenOver):
        await client.async_get_status(CONTRACT)
    assert backend.logins == ["1", "1", "1"]


async def test_failed_forced_login_does_not_start_cooldown(backend, clock):
    # A server error during login isn't a session takeover; the next poll may
    # simply try again.
    calls = []

    def flaky(call):
        calls.append(call)
        return 503, {}

    backend.session.route("POST", const.EP_LOGIN, flaky)
    client = _client(backend)
    for _ in range(2):
        with pytest.raises(SuzukiApiError):
            await client.async_get_vehicles()
    assert len(calls) == 2


# -- redaction ------------------------------------------------------------------

async def test_errors_do_not_leak_credentials(backend, clock):
    client = _client(backend)
    await client.async_get_vehicles()
    token = client.auth.access_token
    secrets = [PASSWORD, token, client.auth.refresh_token, const.CLIENT_SECRET]

    raised = []
    for status, body in [(500, {}), (401, {}), (200, error_envelope(1, "x"))]:
        backend.session.route("POST", const.EP_DASHBOARD, lambda c, s=status, b=body: (s, b))
        try:
            await client.async_get_status(CONTRACT)
        except Exception as err:  # noqa: BLE001
            raised.append(err)
    assert len(raised) == 3
    for err in raised:
        text = f"{err!s} {err!r}"
        for secret in secrets:
            assert secret not in text


async def test_model_reprs_hide_vin_and_location(backend, clock):
    client = _client(backend)
    vehicle, status = await client.async_get_primary_ev_status()
    assert vehicle.vin and vehicle.vin not in repr(vehicle)
    assert status.location and "51.5" not in repr(status)
