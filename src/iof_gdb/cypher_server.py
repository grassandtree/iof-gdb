"""Launch the official mcp-neo4j-cypher server with this project's connection settings.

mcp-neo4j-cypher only reads environment variables, and Claude Code fills ``${VAR}`` in
.mcp.json from the shell it was started in, not from .env. This wrapper reads the settings the
same way as iof-gdb (.env, then the environment wins), passes them to the server through the
environment (never as command-line arguments, which other users can see in ``ps``), and then
replaces itself with ``uvx <package spec> <args>``.

Usage: iof-gdb-cypher [mcp-neo4j-cypher@VERSION] [server args...]
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence

from .config import ConfigError, Settings, load_settings

DEFAULT_SPEC = "mcp-neo4j-cypher@0.6.0"
DEFAULT_ARGS = ("--transport", "stdio")


def build_command(
    settings: Settings, argv: Sequence[str], base_env: Mapping[str, str]
) -> tuple[list[str], dict[str, str]]:
    """Return (command, environment) for the cypher server."""
    args = list(argv) or [DEFAULT_SPEC, *DEFAULT_ARGS]
    if args[0].startswith("-"):  # only server options given: use the default package
        args = [DEFAULT_SPEC, *args]
    uvx = shutil.which("uvx")
    if uvx:
        command = [uvx, *args]
    else:  # uvx missing but uv present: same thing
        uv = shutil.which("uv")
        if not uv:
            raise FileNotFoundError("Neither uvx nor uv is on PATH.")
        command = [uv, "tool", "run", *args]
    env = dict(base_env)
    env.update(
        NEO4J_URI=settings.uri,
        NEO4J_USERNAME=settings.username,
        NEO4J_PASSWORD=settings.password,
        NEO4J_DATABASE=settings.database,
    )
    return command, env


def main(argv: Sequence[str] | None = None) -> int:
    try:
        settings = load_settings()
        command, env = build_command(settings, sys.argv[1:] if argv is None else argv, os.environ)
    except (ConfigError, FileNotFoundError) as e:
        # stdout belongs to the MCP protocol; errors go to stderr
        print(f"iof-gdb-cypher: {e}", file=sys.stderr)
        return 1
    if os.name == "posix":
        os.execve(command[0], command, env)  # keeps the PID Claude Code manages
    return subprocess.call(command, env=env)  # Windows: exec* would detach stdio


if __name__ == "__main__":
    sys.exit(main())
