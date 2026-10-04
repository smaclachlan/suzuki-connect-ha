"""Bundled dashboard cards: served and registered with the frontend."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.suzuki_connect.cards import (
    CARDS_DIR,
    CARDS_FILE,
    CARDS_URL,
    async_register_cards,
)

MANIFEST = CARDS_DIR.parent / "manifest.json"


def test_card_file_is_packaged():
    source = (CARDS_DIR / CARDS_FILE).read_text()
    for tag in ("suzuki-recent-trips-card", "suzuki-charging-sessions-card"):
        assert f'"{tag}"' in source
    assert "window.customCards" in source


def test_cards_register_after_home_assistant_is_defined():
    # HA's app swaps window.customElements for a scoped-registry polyfill
    # after this module may already have run (seen live in Firefox: the
    # cards were listed but "Custom element not found"). Definitions must
    # wait for <home-assistant> and use the registry current at that point.
    source = (CARDS_DIR / CARDS_FILE).read_text()
    assert 'whenDefined("home-assistant").then(register)' in source
    register = source[source.index("function register()"):]
    assert "const registry = window.customElements;" in register
    top_level = source[:source.index("function register()")]
    assert "customElements.define" not in top_level


async def test_registers_versioned_card_url(hass):
    hass.http = MagicMock(async_register_static_paths=AsyncMock())
    hass.config.components.add("frontend")
    with patch("homeassistant.components.frontend.add_extra_js_url") as add_js:
        await async_register_cards(hass)
    [configs], _ = hass.http.async_register_static_paths.call_args
    assert configs[0].url_path == CARDS_URL
    assert configs[0].path == str(CARDS_DIR)
    version = json.loads(MANIFEST.read_text())["version"]
    add_js.assert_called_once_with(hass, f"{CARDS_URL}/{CARDS_FILE}?v={version}")


async def test_without_http_does_nothing(hass):
    hass.http = None
    await async_register_cards(hass)  # no error


async def test_without_frontend_serves_but_does_not_register(hass):
    hass.http = MagicMock(async_register_static_paths=AsyncMock())
    assert "frontend" not in hass.config.components
    with patch("homeassistant.components.frontend.add_extra_js_url") as add_js:
        await async_register_cards(hass)
    hass.http.async_register_static_paths.assert_awaited_once()
    add_js.assert_not_called()
