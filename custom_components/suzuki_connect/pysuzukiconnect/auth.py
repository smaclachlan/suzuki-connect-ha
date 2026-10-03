"""Authentication for Suzuki Connect: login, refresh, and session reclaim.

Session model (see ../docs/API.md): the backend allows one active session per
account. A normal login while another device is active fails with 400008; a
login with override=1 evicts the other device. Reads do not evict. So the
strategy is: refresh when possible, and only full-login (with override) when a
refresh is rejected, to keep evictions of the owner's phone to a minimum.
Forced logins are additionally rate-limited (FORCED_LOGIN_COOLDOWN) so that if
the phone keeps reclaiming the session, a poll fails rather than the two
devices evicting each other several times a minute.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Optional

import aiohttp

from . import const
from .exceptions import (
    SuzukiAnotherActiveLogin,
    SuzukiApiError,
    SuzukiAuthError,
    SuzukiConnectionError,
    SuzukiSessionTakenOver,
)


async def request_json(
    session: aiohttp.ClientSession, method: str, url: str, **kwargs: Any
) -> tuple[int, Any]:
    """Make a request and return (HTTP status, parsed JSON body or None).

    The body is parsed regardless of status because Suzuki reports application
    errors in it, sometimes with odd statuses (e.g. 505 for "Another Active
    Login"). Transport failures become SuzukiConnectionError.
    """
    try:
        async with session.request(method, url, **kwargs) as resp:
            status = resp.status
            try:
                payload = await resp.json(content_type=None)
            except ValueError:
                payload = None  # empty or non-JSON body (e.g. a proxy's HTML page)
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        raise SuzukiConnectionError(
            f"request failed: {type(err).__name__}"
        ) from err
    return status, payload


def first_error(payload: Any) -> Optional[dict]:
    """Return the first entry of the envelope's ``errors`` array, if any."""
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if not errors:
        return None
    err = errors[0] if isinstance(errors, list) else None
    return err if isinstance(err, dict) else {}


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
        # time.monotonic() deadline, immune to wall-clock jumps (NTP, DST).
        self._expires_at: float = 0.0
        # Serialises login/refresh so concurrent callers share one attempt.
        self._lock = asyncio.Lock()
        self._last_forced_login: Optional[float] = None

    # -- public ---------------------------------------------------------
    @property
    def base_url(self) -> str:
        return self._base_url

    async def async_get_access_token(self) -> str:
        """Return a valid access token, refreshing or logging in as needed."""
        if self._token_valid():
            return self.access_token  # type: ignore[return-value]
        async with self._lock:
            # Another caller may have refreshed while we waited for the lock.
            if self._token_valid():
                return self.access_token  # type: ignore[return-value]
            if self.refresh_token:
                try:
                    await self._refresh()
                    return self.access_token  # type: ignore[return-value]
                except SuzukiAuthError:
                    # Refresh rejected (likely evicted by the phone) -> full login.
                    self.refresh_token = None
            self._check_forced_login_allowed()
            await self.async_login(override=True)
            self._last_forced_login = time.monotonic()
            return self.access_token  # type: ignore[return-value]

    def invalidate(self, token: str) -> None:
        """Drop ``token`` after the server rejected it.

        Only clears the token if it is still the current one, so a request that
        was in flight with an old token can't discard a newer one another caller
        just obtained (which would cause a second login).
        """
        if self.access_token == token:
            self.access_token = None
            self._expires_at = 0.0

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
    def _token_valid(self) -> bool:
        return bool(self.access_token) and time.monotonic() < self._expires_at - 30

    def _check_forced_login_allowed(self) -> None:
        if self._last_forced_login is None:
            return
        wait = self._last_forced_login + const.FORCED_LOGIN_COOLDOWN - time.monotonic()
        if wait > 0:
            raise SuzukiSessionTakenOver(retry_after=wait)

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
        # expiresIn units are unconfirmed (observed 240); treat as seconds and
        # clamp to a sane window so a surprising value can't make us either
        # hammer refreshes or hold a token far past its real lifetime. A 401 on
        # any call still triggers a reactive refresh regardless.
        try:
            expires_in = float(_find(data, "expiresIn", "expires_in") or 240)
        except (TypeError, ValueError):
            expires_in = 240.0
        self._expires_at = time.monotonic() + max(60.0, min(expires_in, 3600.0))

    async def _post_form(self, path: str, fields: dict[str, str]) -> dict:
        # aiohttp sets Content-Type: application/x-www-form-urlencoded for a
        # dict passed as `data`, so we only add our own User-Agent.
        headers = {"User-Agent": const.USER_AGENT}
        status, payload = await request_json(
            self._session, "POST", self._base_url + path, data=fields, headers=headers
        )
        self._raise_for_error(status, payload)
        return payload

    @staticmethod
    def _raise_for_error(status: int, payload: Any) -> None:
        # Application errors in the body win over the HTTP status.
        err = first_error(payload)
        if err is not None:
            code = err.get("code")
            if code == const.ERR_ANOTHER_ACTIVE_LOGIN:
                raise SuzukiAnotherActiveLogin(err.get("title"))
            raise SuzukiAuthError(err.get("message") or f"login failed (code {code})")
        if status in (401, 403):
            raise SuzukiAuthError(f"login rejected (HTTP {status})")
        if not 200 <= status < 300:
            # Server-side/transient: must not be mistaken for bad credentials,
            # which would push the user into a reauth flow.
            raise SuzukiApiError(f"login failed (HTTP {status})", status=status)
        if not isinstance(payload, dict):
            raise SuzukiApiError("login returned an unexpected body", status=status)
