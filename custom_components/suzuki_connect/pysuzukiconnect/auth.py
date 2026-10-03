"""Authentication for Suzuki Connect: login, refresh, and session reclaim.

Session model (see ../docs/API.md): the backend allows one active session per
account. A normal login while another device is active fails with 400008; a
login with override=1 evicts the other device. Reads do not evict. So the
strategy is: refresh when possible, and only full-login (with override) when a
refresh is rejected, to keep evictions of the owner's phone to a minimum.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

import aiohttp

from . import const
from .exceptions import (
    SuzukiAnotherActiveLogin,
    SuzukiAuthError,
)


def _find(obj: Any, *names: str) -> Any:
    """Depth-first search for the first non-empty value under any of ``names``."""
    want = {n.lower() for n in names}
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                if isinstance(k, str) and k.lower() in want and v not in (None, "", []):
                    return v
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


class SuzukiAuth:
    """Holds credentials and the current token; performs login/refresh."""

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
        self._email = email
        self._password = password
        self._base_url = base_url.rstrip("/")
        # A stable device id keeps the integration in one of the 5 device slots.
        self.device_id = device_id or str(uuid.uuid4())
        self._device_name = device_name
        self._language = language

        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self._expires_at: float = 0.0

    # -- public ---------------------------------------------------------
    @property
    def base_url(self) -> str:
        return self._base_url

    async def async_get_access_token(self) -> str:
        """Return a valid access token, refreshing or logging in as needed."""
        if self.access_token and time.time() < self._expires_at - 30:
            return self.access_token
        if self.refresh_token:
            try:
                await self._refresh()
                return self.access_token  # type: ignore[return-value]
            except SuzukiAuthError:
                # Refresh rejected (likely evicted by the phone) -> full login.
                self.refresh_token = None
        await self.async_login(override=True)
        return self.access_token  # type: ignore[return-value]

    async def async_login(self, *, override: bool = False) -> None:
        """Password login. override=True evicts whatever device is logged in."""
        fields = {
            "mailID": self._email,
            "password": self._password,
            "grant_type": const.GRANT_PASSWORD,
            "override": const.OVERRIDE_FORCE if override else const.OVERRIDE_OFF,
            "client_id": const.CLIENT_ID,
            "client_secret": const.CLIENT_SECRET,
            "biometric_uuid": "",
            **self._device_fields(),
        }
        data = await self._post_form(const.EP_LOGIN, fields)
        self._store_tokens(data)

    # -- internal -------------------------------------------------------
    def _device_fields(self) -> dict[str, str]:
        return {
            "device_id": self.device_id,
            "device_type": "Android",
            "device_model": self._device_name,
            "os_version": "13",
            "device_token": "",
            "appVer": const.APP_VERSION,
            "preferred_language": self._language,
        }

    async def _refresh(self) -> None:
        fields = {
            "mailID": self._email,
            "grant_type": const.GRANT_REFRESH,
            "client_id": const.CLIENT_ID,
            "client_secret": const.CLIENT_SECRET,
            "access_token": self.access_token or "",
            "refresh_token": self.refresh_token or "",
            **self._device_fields(),
        }
        data = await self._post_form(const.EP_LOGIN, fields)
        self._store_tokens(data)

    def _store_tokens(self, data: dict) -> None:
        token = _find(data, "access_token")
        if not token:
            raise SuzukiAuthError("login/refresh returned no access_token")
        self.access_token = token
        refresh = _find(data, "refresh_token")
        if refresh:
            self.refresh_token = refresh
        # expiresIn units are unconfirmed (observed 240); treat as seconds, which
        # at worst just refreshes a little more often than necessary.
        expires_in = _find(data, "expiresIn", "expires_in") or 240
        try:
            self._expires_at = time.time() + float(expires_in)
        except (TypeError, ValueError):
            self._expires_at = time.time() + 240

    async def _post_form(self, path: str, fields: dict[str, str]) -> dict:
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": const.USER_AGENT,
        }
        async with self._session.post(
            self._base_url + path, data=fields, headers=headers
        ) as resp:
            # The API signals auth errors in the body even with odd HTTP statuses
            # (e.g. 505 for "Another Active Login"), so always parse the body.
            payload = await resp.json(content_type=None)
        self._raise_for_auth_error(payload)
        return payload

    @staticmethod
    def _raise_for_auth_error(payload: dict) -> None:
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if not errors:
            return
        err = errors[0] if isinstance(errors, list) and errors else {}
        code = err.get("code")
        if code == const.ERR_ANOTHER_ACTIVE_LOGIN:
            raise SuzukiAnotherActiveLogin(err.get("title"))
        raise SuzukiAuthError(err.get("message") or f"login failed (code {code})")
