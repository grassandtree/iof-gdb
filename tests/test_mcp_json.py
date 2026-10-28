import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_mcp_json_registers_both_servers_without_secrets():
    raw = (ROOT / ".mcp.json").read_text()
    config = json.loads(raw)["mcpServers"]
    assert set(config) == {"neo4j-cypher", "iof-gdb"}
    # credentials come from .env / the environment via the wrappers, never from this file
    assert "PASSWORD" not in raw and all("env" not in s for s in config.values())

    cypher = config["neo4j-cypher"]
    assert cypher["command"] == "uv" and cypher["args"][:2] == ["run", "iof-gdb-cypher"]
    assert re.fullmatch(r"mcp-neo4j-cypher@\d+\.\d+\.\d+", cypher["args"][2])
    assert config["iof-gdb"]["args"] == ["run", "iof-gdb-mcp"]

    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["iof-gdb-mcp"] == "iof_gdb.server:main"
    assert scripts["iof-gdb-cypher"] == "iof_gdb.cypher_server:main"


def test_wrapper_default_matches_mcp_json():
    from iof_gdb.cypher_server import DEFAULT_SPEC

    config = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]
    assert config["neo4j-cypher"]["args"][2] == DEFAULT_SPEC


def test_dependencies_are_pinned():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    deps = project["project"]["dependencies"] + project["dependency-groups"]["dev"]
    assert deps and all(re.fullmatch(r"[A-Za-z0-9_.-]+==[\w.]+", d) for d in deps)


def test_neo4j_skills_plugin_registered():
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())
    market = settings["extraKnownMarketplaces"]["neo4j-skills-marketplace"]["source"]
    assert market == {"source": "github", "repo": "neo4j-contrib/neo4j-skills"}
    assert settings["enabledPlugins"]["neo4j-skills@neo4j-skills-marketplace"] is True
