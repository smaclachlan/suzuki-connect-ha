"""Constants for the Suzuki Connect integration."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "suzuki_connect"

CONF_DEVICE_ID = "device_id"
CONF_DEVICE_NAME = "device_name"
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"
# Opt-in extra endpoints (each makes additional API calls)
CONF_ENABLE_HEALTH = "enable_health"

DEFAULT_DEVICE_NAME = "HomeAssistant"
# Reads return cached telematics and do not appear to wake the car, but keep the
# cadence gentle by default. Configurable via the options flow.
DEFAULT_SCAN_INTERVAL = timedelta(minutes=15)
MIN_SCAN_INTERVAL_MINUTES = 5
# Vehicle health changes rarely, so fetch it at most this often.
HEALTH_REFRESH = timedelta(hours=1)
