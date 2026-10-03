"""Exceptions for pysuzukiconnect."""
from __future__ import annotations


class SuzukiConnectError(Exception):
    """Base error."""


class SuzukiAuthError(SuzukiConnectError):
    """Login/refresh failed (bad credentials, expired refresh token, etc.)."""


class SuzukiAnotherActiveLogin(SuzukiAuthError):
    """Login refused because another device holds the session (error 400008).

    Retry the login with ``override=True`` to evict the other device.
    """

    def __init__(self, other_device: str | None = None):
        self.other_device = other_device
        super().__init__(
            f"Another device is logged in"
            + (f" ({other_device})" if other_device else "")
            + "; log in with override=True to take over."
        )


class SuzukiConnectionError(SuzukiConnectError):
    """Could not reach the API (network error or timeout)."""


class SuzukiSessionTakenOver(SuzukiConnectError):
    """Another device holds the session and we reclaimed it too recently.

    Deliberately not a SuzukiAuthError: the credentials are fine, so this must
    not trigger a reauth flow. The next poll after ``retry_after`` seconds will
    reclaim the session.
    """

    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(
            "Session was taken over by another device (e.g. the Suzuki app); "
            f"not reclaiming it again for {retry_after:.0f}s."
        )


class SuzukiApiError(SuzukiConnectError):
    """The API returned an error payload for a non-auth call."""

    def __init__(self, message: str, code: int | None = None, status: int | None = None):
        self.code = code
        self.status = status
        super().__init__(message)


class SuzukiNoVehicleError(SuzukiConnectError):
    """No vehicle found on the account."""
