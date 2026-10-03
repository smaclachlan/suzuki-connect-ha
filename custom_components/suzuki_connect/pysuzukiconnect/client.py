"""High-level async client for Suzuki Connect (EU)."""
from __future__ import annotations

from typing import Any, Optional

import aiohttp

from . import const
from .auth import SuzukiAuth
from .exceptions import SuzukiApiError, SuzukiNoVehicleError
from .models import Vehicle, VehicleHealth, VehicleStatus


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
        async with self._session.request(
            method, self._auth.base_url + path, headers=headers, json=json
        ) as resp:
            status = resp.status
            payload = await resp.json(content_type=None)

        # If the session was evicted mid-poll, the token is stale: re-auth once.
        if status in (401, 403) and _retry:
            self._auth.access_token = None
            return await self._authed_request(method, path, json=json, _retry=False)

        errors = payload.get("errors") if isinstance(payload, dict) else None
        if errors:
            err = errors[0] if isinstance(errors, list) and errors else {}
            raise SuzukiApiError(
                err.get("message") or "API error", code=err.get("code"), status=status
            )
        return payload
