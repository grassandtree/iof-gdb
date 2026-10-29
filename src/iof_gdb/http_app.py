"""Streamable HTTP transport for the iof-gdb MCP server (e.g. for Open WebUI).

- DNS rebinding protection stays on: only localhost Host headers are accepted unless more
  are allowed (``host.docker.internal`` when Open WebUI runs in Docker, a LAN name, ...).
- Optional bearer token from the IOF_GDB_MCP_TOKEN environment variable (not a CLI argument,
  so it does not show up in ``ps``).
"""

from __future__ import annotations

import hmac
from collections.abc import Sequence
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

TOKEN_ENV = "IOF_GDB_MCP_TOKEN"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")


def _host_patterns(host: str) -> list[str]:
    """``name`` -> [name, name:*]; ``name:port`` stays as is."""
    if host.endswith("]") or ":" not in host:  # no port (IPv6 literals end with "]")
        return [host, f"{host}:*"]
    return [host]


def transport_security(allowed_hosts: Sequence[str] = ()) -> TransportSecuritySettings:
    """Localhost plus ``allowed_hosts``; ``*`` turns the Host/Origin check off entirely."""
    if "*" in allowed_hosts:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    hosts: list[str] = []
    for host in (*LOCAL_HOSTS, *allowed_hosts):
        hosts.extend(_host_patterns(host))
    origins = [f"{scheme}://{h}" for h in hosts for scheme in ("http", "https")]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(dict.fromkeys(hosts)),
        allowed_origins=list(dict.fromkeys(origins)),
    )


class BearerTokenMiddleware:
    """Reject HTTP requests without ``Authorization: Bearer <token>`` (401)."""

    def __init__(self, app: Any, token: str):
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            given = headers.get(b"authorization", b"")
            if not hmac.compare_digest(given, self.expected):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"text/plain"),
                            (b"www-authenticate", b"Bearer"),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": b"Unauthorized"})
                return
        await self.app(scope, receive, send)


def build_app(
    server: MCPServer,
    *,
    host: str = "127.0.0.1",
    path: str = "/mcp",
    allowed_hosts: Sequence[str] = (),
    token: str | None = None,
) -> Any:
    """ASGI app serving ``server`` over Streamable HTTP at ``path``."""
    app = server.streamable_http_app(
        streamable_http_path=path,
        transport_security=transport_security(allowed_hosts),
        host=host,
    )
    return BearerTokenMiddleware(app, token) if token else app


def serve(server: MCPServer, *, host: str, port: int, **kwargs: Any) -> None:
    import uvicorn

    uvicorn.run(build_app(server, host=host, **kwargs), host=host, port=port, log_level="info")
