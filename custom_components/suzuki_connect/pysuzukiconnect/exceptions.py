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


class SuzukiApiError(SuzukiConnectError):
    """The API returned an error payload for a non-auth call."""

    def __init__(self, message: str, code: int | None = None, status: int | None = None):
        self.code = code
        self.status = status
        super().__init__(message)


class SuzukiNoVehicleError(SuzukiConnectError):
    """No vehicle found on the account."""
