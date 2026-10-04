"""Authentication for Suzuki Connect: login, refresh, and session reclaim.

Session model (see ../docs/API.md): the backend allows one active session per
account. A normal login while another device is active fails with 400008; a
login with override=1 evicts the other device. Reads do not evict. So the
strategy is: refresh when possible, and only full-login (with override) when a
refresh is rejected, to keep evictions of the owner's phone to a minimum.
Forced logins are additionally rate-limited (FORCED_LOGIN_COOLDOWN) so that if
the phone keeps reclaiming the session, a poll fails rather than the two
devices evicting each other several times a minute.

Token lifetime: like the official app, which ignores ``expiresIn`` and only
refreshes when a call returns 401, an access token is used until the server
rejects it. The token is a JWT whose ``exp`` is 240 s after issue, but whether
Suzuki enforces it is unknown, so ``exp`` is only recorded (for diagnostics),
not acted on.
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

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
    kwargs.setdefault("timeout", aiohttp.ClientTimeout(total=const.REQUEST_TIMEOUT))
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


def _login_field(payload: dict, name: str) -> Any:
    """Read a login response field.

    Tokens are top-level in the login response (docs/API.md); also accept the
    standard ``result.data`` envelope, but never search deeper, so a same-named
    key in some unrelated nested object can't be mistaken for a token.
    """
    value = payload.get(name)
    if value in (None, ""):
        result = payload.get("result")
        data = result.get("data") if isinstance(result, dict) else None
        value = data.get(name) if isinstance(data, dict) else None
    return value if value not in (None, "") else None


def jwt_expiry(token: Optional[str]) -> Optional[float]:
    """The ``exp`` claim (Unix time) of a JWT, or None if it isn't one.

    The signature is not checked: this is only a hint for when to refresh.
    """
    try:
        payload = str(token).split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        exp = claims.get("exp") if isinstance(claims, dict) else None
        return float(exp) if isinstance(exp, (int, float)) else None
    except (IndexError, ValueError, TypeError):
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
        # time.monotonic() deadline, immune to wall-clock jumps (NTP, DST);
        # None means no known expiry (use the token until it's rejected).
        self._expires_at: Optional[float] = None
        self._token_obtained_at: Optional[float] = None
        # Serialises login/refresh so concurrent callers share one attempt.
        self._lock = asyncio.Lock()
        self._last_forced_login: Optional[float] = None
        # Called after every successful login/refresh, e.g. to persist the
        # refresh token so a restart can refresh instead of evicting the phone.
        self.on_tokens_updated: Optional[Callable[[], None]] = None
        # When the last login/refresh happened (UTC), for diagnostics only.
        self.last_login_at: Optional[datetime] = None
        self.last_refresh_at: Optional[datetime] = None
        # Token lifetime evidence, for diagnostics: the server's (unused)
        # expiresIn, and how long tokens actually lasted before a 401.
        self.reported_expires_in: Any = None
        self.tokens_rejected = 0
        self.last_rejected_token_age: Optional[float] = None  # seconds

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

    def restore_refresh_token(self, refresh_token: Optional[str]) -> None:
        """Seed a refresh token saved from a previous run (no network call)."""
        if refresh_token and not self.refresh_token:
            self.refresh_token = refresh_token

    @property
    def token_valid(self) -> bool:
        return self._token_valid()

    @property
    def token_expires_in(self) -> Optional[float]:
        """Seconds until the current access token is refreshed proactively;
        None when it has no known expiry (it's used until rejected)."""
        if not self.access_token or self._expires_at is None:
            return None
        return max(0.0, self._expires_at - 30 - time.monotonic())

    @property
    def token_seconds_past_exp(self) -> Optional[float]:
        """How far the current token is past its JWT ``exp`` (negative before
        it). A token still in use well past ``exp`` means Suzuki doesn't
        enforce it."""
        exp = jwt_expiry(self.access_token) if self.access_token else None
        return datetime.now(timezone.utc).timestamp() - exp if exp is not None else None

    @property
    def token_age(self) -> Optional[float]:
        """Seconds since the current access token was issued."""
        if not self.access_token or self._token_obtained_at is None:
            return None
        return time.monotonic() - self._token_obtained_at

    def invalidate(self, token: str) -> None:
        """Drop ``token`` after the server rejected it.

        Only clears the token if it is still the current one, so a request that
        was in flight with an old token can't discard a newer one another caller
        just obtained (which would cause a second login).
        """
        if self.access_token == token:
            self.tokens_rejected += 1
            self.last_rejected_token_age = self.token_age
            self.access_token = None
            self._expires_at = None

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
        self.last_login_at = datetime.now(timezone.utc)
        self._notify_tokens_updated()

    # -- internal -------------------------------------------------------
    def _token_valid(self) -> bool:
        if not self.access_token:
            return False
        return self._expires_at is None or time.monotonic() < self._expires_at - 30

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
        self.last_refresh_at = datetime.now(timezone.utc)
        self._notify_tokens_updated()

    def _notify_tokens_updated(self) -> None:
        if self.on_tokens_updated is not None:
            self.on_tokens_updated()

    def _store_tokens(self, data: dict) -> None:
        token = _login_field(data, "access_token")
        if not token:
            raise SuzukiAuthError("login/refresh returned no access_token")
        self.access_token = token
        refresh = _login_field(data, "refresh_token")
        if refresh:
            self.refresh_token = refresh
        now = time.monotonic()
        self._token_obtained_at = now
        # expiresIn (observed 240, units unknown) is recorded but not used: the
        # official app ignores it too and refreshes only on a 401.
        self.reported_expires_in = _login_field(data, "expiresIn")
        # No proactive expiry: refresh only on 401 (see module docstring).
        self._expires_at = None

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
        err = first_error(payload)
        if err is not None:
            code = err.get("code")
            if code == const.ERR_ANOTHER_ACTIVE_LOGIN:
                # Suzuki sends this one over HTTP 505, so check it before status.
                raise SuzukiAnotherActiveLogin(err.get("title"))
            message = err.get("message") or f"login failed (code {code})"
            if status >= 500:
                # A server-side error body (e.g. maintenance) is not a credential
                # problem: treating it as one would discard a working refresh
                # token, force a login that evicts the phone, or start reauth.
                raise SuzukiApiError(message, code=code, status=status)
            raise SuzukiAuthError(message)
        if status in (401, 403):
            raise SuzukiAuthError(f"login rejected (HTTP {status})")
        if not 200 <= status < 300:
            # Server-side/transient: must not be mistaken for bad credentials,
            # which would push the user into a reauth flow.
            raise SuzukiApiError(f"login failed (HTTP {status})", status=status)
        if not isinstance(payload, dict):
            raise SuzukiApiError("login returned an unexpected body", status=status)
