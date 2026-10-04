"""A tiny stand-in for aiohttp.ClientSession, so client tests need no network
and no extra test dependencies."""
from __future__ import annotations

import asyncio
import json as jsonlib
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import aiohttp


class FakeResponse:
    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        # str bodies are sent verbatim (e.g. an HTML error page); anything else
        # is JSON-encoded.
        self._text = body if isinstance(body, str) else jsonlib.dumps(body)

    async def json(self, content_type: Any = None) -> Any:
        return jsonlib.loads(self._text)


class _RequestContext:
    def __init__(self, coro) -> None:
        self._coro = coro

    async def __aenter__(self) -> FakeResponse:
        return await self._coro

    async def __aexit__(self, *exc) -> None:
        return None


class FakeSession:
    """Routes (method, path) to a handler ``fn(call) -> (status, body)``.

    ``call`` is a dict with method, path, data, json and headers. A handler may
    also raise (e.g. aiohttp.ClientError) to simulate transport failures.
    Every request yields to the event loop first so concurrent callers really
    interleave.
    """

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Callable[[dict], tuple[int, Any]]] = {}
        self.calls: list[dict] = []

    def route(self, method: str, path: str, fn: Callable[[dict], tuple[int, Any]]):
        self.routes[(method, path)] = fn

    def calls_to(self, path: str) -> list[dict]:
        return [c for c in self.calls if c["path"] == path]

    def request(self, method: str, url: str, **kwargs: Any) -> _RequestContext:
        return _RequestContext(self._do(method, url, **kwargs))

    def post(self, url: str, **kwargs: Any) -> _RequestContext:
        return self.request("POST", url, **kwargs)

    async def _do(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        await asyncio.sleep(0)
        path = urlsplit(url).path
        call = {
            "method": method,
            "path": path,
            "data": kwargs.get("data"),
            "json": kwargs.get("json"),
            "headers": kwargs.get("headers") or {},
            "timeout": kwargs.get("timeout"),
        }
        self.calls.append(call)
        fn = self.routes.get((method, path))
        if fn is None:
            raise aiohttp.ClientConnectionError(f"no route for {method} {path}")
        status, body = fn(call)
        return FakeResponse(status, body)


def envelope(data: dict | None = None, **top: Any) -> dict:
    """Suzuki's standard success envelope."""
    return {"errors": [], "result": {"message": "", "title": "", "data": data or {}}, **top}


def error_envelope(code: int, message: str = "", title: str = "") -> dict:
    return {"errors": [{"code": code, "message": message, "title": title}], "result": {}}


# Mirrors pysuzukiconnect.const; duplicated so these helpers work whichever way
# the library is imported (top-level in library tests, as part of
# custom_components.suzuki_connect in HA tests).
EP_LOGIN = "/api/sconnectapp/login/token"
EP_VEHICLE_DETAILS = "/api/profile/vehicleDetailsAuth"
EP_DASHBOARD = "/api/dashboard/dashboardOauth"
EP_VEHICLE_HEALTH = "/api/dashboard/vehicleHealthStatus/{contract_id}"
GRANT_REFRESH = "refresh_token"

FIXTURES = Path(__file__).parent / "fixtures"
PASSWORD = "hunter2-secret"


def load_fixture(name: str) -> Any:
    return jsonlib.loads((FIXTURES / name).read_text())


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
        # Every access token ever issued: a refresh must send one of these
        # (expired is fine), as Suzuki appears to require.
        self.issued_access: set[str] = set()
        self.refresh_needs_access_token = True
        self.logins: list[str] = []  # override flag per password login
        self.refreshes = 0
        self.refresh_survives_eviction = False
        session.route("POST", EP_LOGIN, self._login)

    def _mint(self) -> dict:
        self.counter += 1
        access, refresh = f"access-{self.counter}", f"refresh-{self.counter}"
        self.valid_access.add(access)
        self.valid_refresh.add(refresh)
        self.issued_access.add(access)
        return envelope(access_token=access, refresh_token=refresh, expiresIn=240)

    def _login(self, call):
        form = call["data"]
        if form["grant_type"] == GRANT_REFRESH:
            self.refreshes += 1
            if form["refresh_token"] not in self.valid_refresh:
                return 400, error_envelope(400001, "Invalid refresh token")
            if self.refresh_needs_access_token and form["access_token"] not in self.issued_access:
                return 400, error_envelope(400003, "Invalid access token")
            return 200, self._mint()
        self.logins.append(form["override"])
        if form["password"] != PASSWORD:
            return 400, error_envelope(400002, "Invalid credentials")
        self.valid_access.clear()
        self.valid_refresh.clear()
        return 200, self._mint()

    def expire_access(self) -> None:
        """The access token times out server-side; the refresh token still works."""
        self.valid_access.clear()

    def evict(self) -> None:
        self.valid_access.clear()
        if not self.refresh_survives_eviction:
            self.valid_refresh.clear()

    def authorised(self, call) -> bool:
        auth = call["headers"].get("Authorization", "")
        return auth.removeprefix("Bearer ") in self.valid_access


def make_backend(
    vehicles: Any = "vehicles.json", dashboard: Any = "dashboard.json"
) -> FakeBackend:
    """A FakeBackend serving the vehicle list and dashboard behind auth.

    ``vehicles``/``dashboard`` are fixture names or already-loaded payloads.
    """
    be = FakeBackend(FakeSession())

    def data_route(payload):
        def fn(call):
            if not be.authorised(call):
                return 401, {"message": "Unauthorized"}
            return 200, load_fixture(payload) if isinstance(payload, str) else payload
        return fn

    be.session.route("GET", EP_VEHICLE_DETAILS, data_route(vehicles))
    be.session.route("POST", EP_DASHBOARD, data_route(dashboard))
    return be
