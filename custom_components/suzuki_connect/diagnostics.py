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
from homeassistant.util import dt as dt_util

from . import SuzukiConfigEntry
from .const import CONF_CONTRACT_ID, CONF_CONTRACT_IDS, CONF_DEVICE_ID
from .pysuzukiconnect.auth import jwt_expiry

REDACTED = "**REDACTED**"

# Credentials/identity, vehicle identifiers, and anything location-like.
REDACT_WORDS = frozenset({
    "password", "pin", "token", "secret", "key", "mail", "email", "phone",
    "mobile", "msisdn", "imei", "iccid", "unique", "tracking",
    "contact", "name", "address", "addr", "vin", "vrn", "contract", "gps",
    "lat", "latitude", "lon", "long", "lng", "longitude", "geo", "geofence",
    "location", "dealer",
    # Signed (credential-bearing) image URLs.
    "image",
})

# Exact-match keys (our config entry, and ids that only redact as a pair).
REDACT_KEYS = frozenset({
    CONF_EMAIL, CONF_PASSWORD, CONF_DEVICE_ID, CONF_CONTRACT_ID, CONF_CONTRACT_IDS,
    "deviceId", "unique_id",
    # Encodes an account number and trip start time.
    "trip_id",
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


def _round(value: float | None) -> int | None:
    return round(value) if value is not None else None


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SuzukiConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    auth = coordinator.client.auth
    data = coordinator.data

    return {
        # The integration reads the car's naive timestamps (lut) in this zone.
        "ha_time_zone": hass.config.time_zone,
        "ha_now": dt_util.now().isoformat(),
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
            # None: no known expiry, so the token is used until rejected.
            "access_token_expires_in_s": (
                round(auth.token_expires_in)
                if auth.token_expires_in is not None else None
            ),
            "access_token_is_jwt_with_exp": jwt_expiry(auth.access_token) is not None,
            "access_token_age_s": _round(auth.token_age),
            # Positive: the token is still in use past its JWT exp.
            "access_token_seconds_past_jwt_exp": _round(auth.token_seconds_past_exp),
            # Evidence for the real token lifetime (expiresIn is not used).
            "reported_expires_in": auth.reported_expires_in,
            "tokens_rejected": auth.tokens_rejected,
            "last_rejected_token_age_s": _round(auth.last_rejected_token_age),
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
            # Exactly as Suzuki sent it, to check its timezone and format.
            "lut_raw": vdata.status.raw_meta.get("lut"),
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
        "dashboard": redact(vdata.status.raw_meta),
        "health": redact(vdata.health.raw) if vdata.health else None,
        "extended": _extended_diagnostics(vdata.extended),
    }


def _extended_diagnostics(ext) -> dict[str, Any] | None:
    """Raw (redacted) extended payloads, to confirm their unverified shapes."""
    if ext is None:
        return None
    parts = ("driving", "charging", "charge_schedules", "climate_schedules", "subscription")
    return {
        "trips_parsed": len(ext.trips) if ext.trips is not None else None,
        **{p: redact(getattr(ext, p).raw) if getattr(ext, p) else None for p in parts},
    }
