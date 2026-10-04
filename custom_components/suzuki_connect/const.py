"""Constants for the Suzuki Connect integration."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "suzuki_connect"

CONF_DEVICE_ID = "device_id"
CONF_DEVICE_NAME = "device_name"
# Selected vehicles (list of contract ids). CONF_CONTRACT_ID is the single id
# stored by config entry minor version 1, migrated to the list in 1.2.
CONF_CONTRACT_IDS = "contract_ids"
CONF_CONTRACT_ID = "contract_id"
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"
CONF_SLOW_INTERVAL_MINUTES = "slow_interval_minutes"
# Opt-in extra endpoints (each makes additional API calls)
CONF_ENABLE_HEALTH = "enable_health"
CONF_ENABLE_EXTENDED = "enable_extended"

DEFAULT_DEVICE_NAME = "HomeAssistant"
# Live status. Reads return cached telematics and do not appear to wake the car;
# a moving car reports about once a minute, so 1 minute is the useful minimum
# (short trips can fall between slower polls). The default stays gentle on
# Suzuki's API. Configurable via the options flow.
DEFAULT_SCAN_INTERVAL = timedelta(minutes=15)
MIN_SCAN_INTERVAL_MINUTES = 1
MAX_SCAN_INTERVAL_MINUTES = 240
# Vehicle health changes rarely, so fetch it at most this often.
HEALTH_REFRESH = timedelta(hours=1)
# Slow path: the vehicle list, plus (when enabled) trips, charging history,
# schedules and subscription. Slow-changing, so on their own, longer interval.
DEFAULT_SLOW_INTERVAL = timedelta(hours=6)
MIN_SLOW_INTERVAL_MINUTES = 30
MAX_SLOW_INTERVAL_MINUTES = 24 * 60

# Per-entry storage (persisted refresh token).
STORAGE_VERSION = 1
