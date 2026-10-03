"""pysuzukiconnect — unofficial async client for the Suzuki Connect (EU) car API.

Example:

    import aiohttp, asyncio
    from pysuzukiconnect import SuzukiConnectClient

    async def main():
        async with aiohttp.ClientSession() as s:
            client = SuzukiConnectClient(s, "you@example.com", "password")
            vehicle, status = await client.async_get_primary_ev_status()
            print(vehicle.brand, status.state_of_charge, "%")

    asyncio.run(main())
"""
from __future__ import annotations

from .client import SuzukiConnectClient
from .exceptions import (
    SuzukiAnotherActiveLogin,
    SuzukiApiError,
    SuzukiAuthError,
    SuzukiConnectError,
    SuzukiNoVehicleError,
)
from .models import Vehicle, VehicleHealth, VehicleStatus

__version__ = "0.1.0"

__all__ = [
    "SuzukiConnectClient",
    "Vehicle",
    "VehicleStatus",
    "VehicleHealth",
    "SuzukiConnectError",
    "SuzukiAuthError",
    "SuzukiAnotherActiveLogin",
    "SuzukiApiError",
    "SuzukiNoVehicleError",
]
