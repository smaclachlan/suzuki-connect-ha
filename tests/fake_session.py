"""A tiny stand-in for aiohttp.ClientSession, so client tests need no network
and no extra test dependencies."""
from __future__ import annotations

import asyncio
import json as jsonlib
from typing import Any, Callable
from urllib.parse import urlsplit

import aiohttp


class FakeResponse:
    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        # str bodies are sent verbatim (e.g. an HTML error page); anything else
        # is JSON-encoded.
        self._text = body if isinstance(body, str) else jsonlib.dumps(body)

    async def json(self, content_type: Any = None) -> Any:
        return jsonlib.loads(self._text)


class _RequestContext:
    def __init__(self, coro) -> None:
        self._coro = coro

    async def __aenter__(self) -> FakeResponse:
        return await self._coro

    async def __aexit__(self, *exc) -> None:
        return None


class FakeSession:
    """Routes (method, path) to a handler ``fn(call) -> (status, body)``.

    ``call`` is a dict with method, path, data, json and headers. A handler may
    also raise (e.g. aiohttp.ClientError) to simulate transport failures.
    Every request yields to the event loop first so concurrent callers really
    interleave.
    """

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Callable[[dict], tuple[int, Any]]] = {}
        self.calls: list[dict] = []

    def route(self, method: str, path: str, fn: Callable[[dict], tuple[int, Any]]):
        self.routes[(method, path)] = fn

    def calls_to(self, path: str) -> list[dict]:
        return [c for c in self.calls if c["path"] == path]

    def request(self, method: str, url: str, **kwargs: Any) -> _RequestContext:
        return _RequestContext(self._do(method, url, **kwargs))

    def post(self, url: str, **kwargs: Any) -> _RequestContext:
        return self.request("POST", url, **kwargs)

    async def _do(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        await asyncio.sleep(0)
        path = urlsplit(url).path
        call = {
            "method": method,
            "path": path,
            "data": kwargs.get("data"),
            "json": kwargs.get("json"),
            "headers": kwargs.get("headers") or {},
        }
        self.calls.append(call)
        fn = self.routes.get((method, path))
        if fn is None:
            raise aiohttp.ClientConnectionError(f"no route for {method} {path}")
        status, body = fn(call)
        return FakeResponse(status, body)


def envelope(data: dict | None = None, **top: Any) -> dict:
    """Suzuki's standard success envelope."""
    return {"errors": [], "result": {"message": "", "title": "", "data": data or {}}, **top}


def error_envelope(code: int, message: str = "", title: str = "") -> dict:
    return {"errors": [{"code": code, "message": message, "title": title}], "result": {}}
