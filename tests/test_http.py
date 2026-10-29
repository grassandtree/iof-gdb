"""Streamable HTTP transport (Open WebUI and other HTTP MCP clients)."""

import asyncio
import os
import socket
import subprocess
import sys
import time

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from iof_gdb import http_app

from .test_server import EXPECTED_TOOLS

TOKEN = "test-token-123"
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "0"},
    },
}
HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


def test_transport_security_patterns():
    s = http_app.transport_security(["host.docker.internal", "mcp.lan:8443"])
    assert s.enable_dns_rebinding_protection
    for host in ("localhost:*", "127.0.0.1:*", "host.docker.internal", "host.docker.internal:*"):
        assert host in s.allowed_hosts
    assert "mcp.lan:8443" in s.allowed_hosts and "mcp.lan:8443:*" not in s.allowed_hosts
    assert "http://host.docker.internal:*" in s.allowed_origins
    assert not http_app.transport_security(["*"]).enable_dns_rebinding_protection


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def http_server(tmp_path_factory):
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith("NEO4J_")}
    env[http_app.TOKEN_ENV] = TOKEN
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "iof_gdb.server",
            "--transport",
            "streamable-http",
            "--port",
            str(port),
            "--allowed-host",
            "host.docker.internal",
        ],
        env=env,
        cwd=tmp_path_factory.mktemp("srv"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("HTTP server did not start")
    yield f"http://127.0.0.1:{port}/mcp"
    proc.terminate()
    proc.wait(timeout=10)


def test_requires_bearer_token(http_server):
    r = httpx2.post(http_server, json=INIT, headers=HEADERS)
    assert r.status_code == 401
    r = httpx2.post(http_server, json=INIT, headers={**HEADERS, "Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_host_header_check(http_server):
    auth = {**HEADERS, "Authorization": f"Bearer {TOKEN}"}
    port = http_server.split(":")[2].split("/")[0]
    r = httpx2.post(http_server, json=INIT, headers={**auth, "Host": f"evil.example:{port}"})
    assert r.status_code == 421
    r = httpx2.post(
        http_server, json=INIT, headers={**auth, "Host": f"host.docker.internal:{port}"}
    )
    assert r.status_code == 200


def test_mcp_client_over_http(http_server):
    async def go():
        http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {TOKEN}"})
        transport = streamable_http_client(http_server, http_client=http)
        async with Client(transport, mode="legacy") as client:
            tools = {t.name for t in (await client.list_tools()).tools}
            res = await client.read_resource("iof://prefixes")
            return tools, res.contents[0].text

    tools, prefixes = asyncio.run(go())
    assert tools == EXPECTED_TOOLS
    assert "iofconstr" in prefixes
