"""Serve and register the bundled dashboard cards (frontend/suzuki-cards.js).

The cards then appear in the dashboard card picker without a separate HACS
install. The URL carries the integration version so browsers fetch the new
file after an update despite long cache headers.
"""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CARDS_URL = f"/{DOMAIN}/cards"
CARDS_FILE = "suzuki-cards.js"
CARDS_DIR = Path(__file__).parent / "frontend"


async def async_register_cards(hass: HomeAssistant) -> None:
    """Serve the cards and add them to the frontend, once per HA start."""
    if getattr(hass, "http", None) is None:
        return  # no web server (e.g. some test setups): nothing to serve
    from homeassistant.components.http import StaticPathConfig

    integration = await async_get_integration(hass, DOMAIN)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARDS_URL, str(CARDS_DIR), cache_headers=True)]
    )
    if "frontend" not in hass.config.components:
        _LOGGER.debug("Frontend not loaded; dashboard cards not registered")
        return
    from homeassistant.components.frontend import add_extra_js_url

    add_extra_js_url(hass, f"{CARDS_URL}/{CARDS_FILE}?v={integration.version}")
