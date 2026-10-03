"""Fixtures for Home Assistant integration tests."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from fake_session import FakeBackend, make_backend


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def backend() -> FakeBackend:
    return make_backend()


@pytest.fixture
def patch_session(backend):
    """Route the integration's HTTP traffic to the fake backend."""
    with (
        patch(
            "custom_components.suzuki_connect.coordinator.async_get_clientsession",
            return_value=backend.session,
        ),
        patch(
            "custom_components.suzuki_connect.config_flow.async_get_clientsession",
            return_value=backend.session,
        ),
    ):
        yield backend
