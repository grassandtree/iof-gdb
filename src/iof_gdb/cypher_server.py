"""Launch the official mcp-neo4j-cypher server with this project's connection settings.

mcp-neo4j-cypher only reads environment variables, and Claude Code fills ``${VAR}`` in
.mcp.json from the shell it was started in, not from .env. This wrapper reads the settings the
same way as iof-gdb (.env, then the environment wins), passes them to the server through the
environment (never as command-line arguments, which other users can see in ``ps``), and then
replaces itself with ``uvx <package spec> <args>``.

Without uv (e.g. an offline Windows PC with only pip and a PyPI mirror), install
mcp-neo4j-cypher into its own venv from requirements-cypher.txt and point IOF_GDB_CYPHER_EXE at
its executable; the wrapper then runs that file directly. The venv must be separate because
mcp-neo4j-cypher needs mcp 1.x (fastmcp 2.13) while this project uses mcp 2.x, which is the
isolation uvx otherwise provides. In that mode the package spec argument is ignored.

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
EXE_VAR = "IOF_GDB_CYPHER_EXE"


def build_command(
    settings: Settings, argv: Sequence[str], base_env: Mapping[str, str]
) -> tuple[list[str], dict[str, str]]:
    """Return (command, environment) for the cypher server."""
    args = list(argv) or [DEFAULT_SPEC, *DEFAULT_ARGS]
    if args[0].startswith("-"):  # only server options given: use the default package
        args = [DEFAULT_SPEC, *args]
    exe = base_env.get(EXE_VAR)
    if exe:  # installed mcp-neo4j-cypher (no uv): drop the package spec
        if not os.path.isfile(exe):
            raise FileNotFoundError(f"{EXE_VAR} does not point to a file: {exe}")
        command = [exe, *args[1:]]
    elif uvx := shutil.which("uvx"):
        command = [uvx, *args]
    else:  # uvx missing but uv present: same thing
        uv = shutil.which("uv")
        if not uv:
            raise FileNotFoundError(
                f"Neither uvx nor uv is on PATH; set {EXE_VAR} to an installed mcp-neo4j-cypher."
            )
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
