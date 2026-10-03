"""Diagnostics for Suzuki Connect.

Raw API payloads aren't fully mapped, so redaction can't rely on an exact key
list: keys are split into words (snake_case and camelCase) and any key with a
word in REDACT_WORDS is redacted wherever it appears. Matching whole words
avoids false hits like "vin" in "driving_range"; otherwise this over-redacts on
purpose.
"""
from __future__ import annotations

import re
from typing import Any

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import SuzukiConfigEntry
from .const import CONF_CONTRACT_ID, CONF_CONTRACT_IDS, CONF_DEVICE_ID

REDACTED = "**REDACTED**"

# Credentials/identity, vehicle identifiers, and anything location-like.
REDACT_WORDS = frozenset({
    "password", "pin", "token", "secret", "key", "mail", "email", "phone",
    "contact", "name", "address", "addr", "vin", "vrn", "contract", "gps",
    "lat", "latitude", "lon", "lng", "longitude", "geo", "geofence",
    "location", "dealer",
})

# Exact-match keys (our config entry, and ids that only redact as a pair).
REDACT_KEYS = frozenset({
    CONF_EMAIL, CONF_PASSWORD, CONF_DEVICE_ID, CONF_CONTRACT_ID, CONF_CONTRACT_IDS,
    "deviceId", "unique_id",
})

_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def _should_redact(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    if key in REDACT_KEYS:
        return True
    return any(word.lower() in REDACT_WORDS for word in _WORD.findall(key))


def redact(value: Any) -> Any:
    """Recursively redact sensitive keys in dicts/lists."""
    if isinstance(value, dict):
        return {
            k: (REDACTED if _should_redact(k) and v not in (None, "") else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SuzukiConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    auth = coordinator.client.auth
    data = coordinator.data

    return {
        "entry": {
            "data": redact(dict(entry.data)),
            "options": dict(entry.options),
        },
        "polling": {
            "update_interval_s": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval else None
            ),
            "last_update_success": coordinator.last_update_success,
            "last_attempt": _iso(coordinator.last_attempt),
            "last_successful_poll": _iso(coordinator.last_polled),
            "last_latency_s": (
                round(coordinator.last_latency, 3)
                if coordinator.last_latency is not None else None
            ),
            "consecutive_failures": coordinator.consecutive_failures,
            "last_error": coordinator.last_error,
        },
        "auth": {
            "has_access_token": auth.access_token is not None,
            "access_token_valid": auth.token_valid,
            "access_token_expires_in_s": (
                round(auth.token_expires_in)
                if auth.token_expires_in is not None else None
            ),
            "has_refresh_token": auth.refresh_token is not None,
            "last_login": _iso(auth.last_login_at),
            "last_refresh": _iso(auth.last_refresh_at),
        },
        # Listed in order, not keyed by contract id (which is redacted).
        "vehicles": [
            _vehicle_diagnostics(coordinator, cid, vdata)
            for cid, vdata in (data.vehicles.items() if data else ())
        ],
    }


def _vehicle_diagnostics(coordinator, contract_id: int, vdata) -> dict[str, Any]:
    age = coordinator.telemetry_age(contract_id)
    settings = coordinator.settings_for(contract_id)
    return {
        "telemetry": {
            "last_reported_by_car": _iso(
                coordinator.vehicle_time(vdata.status.last_updated)
            ),
            "age_s": round(age.total_seconds()) if age is not None else None,
        },
        "settings": {
            "battery_capacity_kwh": settings.battery_capacity,
            "charge_target_pct": settings.charge_target,
        },
        "vehicle": redact(vdata.vehicle.raw),
        "status": redact(vdata.status.raw),
        "health": redact(vdata.health.raw) if vdata.health else None,
    }
