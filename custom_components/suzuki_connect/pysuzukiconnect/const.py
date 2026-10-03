"""Constants for the Suzuki Connect (EU) API.

Values reverse-engineered from the official EU app (suzuki.app.a025.SzkCnnctEur).
The client_id / client_secret are app-level identifiers shared by every install,
not per-user secrets — the same model the Toyota/Kia/Hyundai HA integrations use.
See ../docs/API.md for the full map.
"""
from __future__ import annotations

# EU production. Pre-prod uses *.sc.pre-eur.connect.suzuki; dev *.suzukiconnect.info.
BASE_URL = "https://en01cs.sc.eur.connect.suzuki"

CLIENT_ID = "ADFEF11CFE9F4E17A224CCF3AD652"
CLIENT_SECRET = "Mofra6j4HkQNf1sZEgCg5eKGPHjRiZJ1"

APP_VERSION = "1.0.53"
USER_AGENT = "okhttp/4.12.0"
DEFAULT_DEVICE_NAME = "HomeAssistant"
DEFAULT_LANGUAGE = "EN"

# Endpoints
EP_LOGIN = "/api/sconnectapp/login/token"
EP_VEHICLE_DETAILS = "/api/profile/vehicleDetailsAuth"
EP_DASHBOARD = "/api/dashboard/dashboardOauth"
EP_LOGOUT = "/api/logout"

# grant types / login
GRANT_PASSWORD = "password"
GRANT_REFRESH = "refresh_token"
OVERRIDE_OFF = "0"
OVERRIDE_FORCE = "1"  # evicts the currently-logged-in device

# Known application error codes
ERR_ANOTHER_ACTIVE_LOGIN = 400008

# Charging status values seen in charge_st (to be expanded as observed)
CHARGE_ST_NOT_CHARGING = 0
