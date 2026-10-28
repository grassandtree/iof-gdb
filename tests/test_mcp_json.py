import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_mcp_json_registers_both_servers_without_secrets():
    config = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]
    assert set(config) == {"neo4j-cypher", "iof-gdb"}

    cypher = config["neo4j-cypher"]
    assert re.fullmatch(r"mcp-neo4j-cypher@\d+\.\d+\.\d+", cypher["args"][0])
    # credentials only via environment variable expansion
    for value in cypher["env"].values():
        assert value.startswith("${") and value.endswith("}")

    assert config["iof-gdb"]["args"] == ["run", "iof-gdb-mcp"]
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["iof-gdb-mcp"] == "iof_gdb.server:main"


def test_dependencies_are_pinned():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    deps = project["project"]["dependencies"] + project["dependency-groups"]["dev"]
    assert deps and all(re.fullmatch(r"[A-Za-z0-9_.-]+==[\w.]+", d) for d in deps)
