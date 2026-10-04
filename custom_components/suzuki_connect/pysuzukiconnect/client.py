"""High-level async client for Suzuki Connect (EU)."""
from __future__ import annotations

import re
from typing import Any, Optional

import aiohttp

from . import const
from .auth import SuzukiAuth, first_error, request_json
from .exceptions import SuzukiApiError, SuzukiNoVehicleError
from .models import (
    ChargingHistory,
    DrivingHistory,
    Schedules,
    Subscription,
    Vehicle,
    VehicleHealth,
    VehicleStatus,
)


class SuzukiConnectClient:
    """Read client for a Suzuki Connect account.

    Pass an existing ``aiohttp.ClientSession`` (Home Assistant supplies one).
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        *,
        base_url: str = const.BASE_URL,
        device_id: Optional[str] = None,
        device_name: str = const.DEFAULT_DEVICE_NAME,
        language: str = const.DEFAULT_LANGUAGE,
    ) -> None:
        self._session = session
        self._auth = SuzukiAuth(
            session, email, password,
            base_url=base_url, device_id=device_id,
            device_name=device_name, language=language,
        )

    @property
    def auth(self) -> SuzukiAuth:
        return self._auth

    async def async_get_vehicles(self) -> list[Vehicle]:
        """Return every vehicle on the account (both primary and secondary)."""
        data = await self._authed_request("GET", const.EP_VEHICLE_DETAILS)
        vdata = self._result_data(data).get("VEHICLE_DATA", {}) or {}
        entries = (vdata.get("PRIMARY_VEHICLE_LIST") or []) + \
                  (vdata.get("SECONDARY_VEHICLE_LIST") or [])
        vehicles = [Vehicle.from_entry(e) for e in entries if e.get("CONTRACT_ID")]
        if not vehicles:
            raise SuzukiNoVehicleError("no vehicle found on account")
        return vehicles

    async def async_get_status(self, contract_id: int) -> VehicleStatus:
        """Fetch live EV/body status for a contract id."""
        body = {
            "contract_id": int(contract_id),
            "deviceId": self._auth.device_id,
            "origin": "DEVICE",
        }
        data = await self._authed_request("POST", const.EP_DASHBOARD, json=body)
        dashboard = self._result_data(data).get("DASHBOARD_DATA", {}) or {}
        return VehicleStatus.from_dashboard(dashboard)

    async def async_get_vehicle_health(self, contract_id: int) -> VehicleHealth:
        """Fetch the vehicle health summary (separate, slower endpoint)."""
        data = await self._authed_request(
            "GET", const.EP_VEHICLE_HEALTH.format(contract_id=int(contract_id))
        )
        return VehicleHealth.from_response(data)

    async def async_get_driving_history(self, month: str) -> DrivingHistory:
        """Trips for a calendar month (``yyyy-MM``), across the whole account."""
        if not re.fullmatch(r"\d{4}-\d{2}", month):
            raise ValueError("month must be yyyy-MM")
        data = await self._authed_request(
            "GET", const.EP_DRIVING_HISTORY.format(month=month)
        )
        return DrivingHistory.from_response(data)

    async def async_get_charging_history(self, contract_id: int) -> ChargingHistory:
        # "default": "0" is what the app sends when opening the history screen.
        body = {"contractId": int(contract_id), "default": "0"}
        data = await self._authed_request("POST", const.EP_CHARGING_HISTORY, json=body)
        return ChargingHistory.from_response(data)

    async def async_get_charge_schedules(self, contract_id: int) -> Schedules:
        body = {"contractID": int(contract_id)}
        data = await self._authed_request("POST", const.EP_CHARGE_SCHEDULES, json=body)
        return Schedules.charge_from_response(data)

    async def async_get_climate_schedules(self, contract_id: int) -> Schedules:
        data = await self._authed_request(
            "GET", const.EP_CLIMATE_SCHEDULES.format(contract_id=int(contract_id))
        )
        return Schedules.climate_from_response(data)

    async def async_get_subscription(self, contract_id: int) -> Subscription:
        data = await self._authed_request(
            "GET", const.EP_SUBSCRIPTION.format(contract_id=int(contract_id))
        )
        return Subscription.from_response(data)

    async def async_get_primary_ev_status(self) -> tuple[Vehicle, VehicleStatus]:
        """Convenience: first EV (or first vehicle) plus its status."""
        vehicles = await self.async_get_vehicles()
        vehicle = next((v for v in vehicles if v.is_ev), vehicles[0])
        status = await self.async_get_status(vehicle.contract_id)
        return vehicle, status

    # -- internals ------------------------------------------------------
    @staticmethod
    def _result_data(payload: dict) -> dict:
        result = payload.get("result") if isinstance(payload, dict) else None
        if isinstance(result, dict):
            return result.get("data", {}) or {}
        return {}

    async def _authed_request(
        self, method: str, path: str, *, json: Any = None, _retry: bool = True
    ) -> dict:
        token = await self._auth.async_get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": const.USER_AGENT,
        }
        status, payload = await request_json(
            self._session, method, self._auth.base_url + path,
            headers=headers, json=json,
        )

        # If the session was evicted mid-poll, the token is stale: re-auth once.
        if status in (401, 403) and _retry:
            self._auth.invalidate(token)
            return await self._authed_request(method, path, json=json, _retry=False)

        # Application errors in the body take precedence (they carry a code and
        # a readable message, and Suzuki sometimes sends them with odd statuses).
        err = first_error(payload)
        if err is not None:
            raise SuzukiApiError(
                err.get("message") or "API error", code=err.get("code"), status=status
            )
        if not 200 <= status < 300:
            raise SuzukiApiError(f"HTTP {status}", status=status)
        if not isinstance(payload, dict):
            raise SuzukiApiError("unexpected response body", status=status)
        return payload
