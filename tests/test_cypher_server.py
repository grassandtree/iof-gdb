import pytest

from iof_gdb import cypher_server as cs
from iof_gdb.config import ConfigError, Settings

SETTINGS = Settings(
    uri="neo4j+s://x.databases.neo4j.io", username="neo4j", password="s3cret", database="abc123"
)


@pytest.fixture
def uvx(monkeypatch):
    monkeypatch.setattr(cs.shutil, "which", lambda name: f"/bin/{name}")


def test_build_command_passes_credentials_via_env_only(uvx):
    cmd, env = cs.build_command(
        SETTINGS, ["mcp-neo4j-cypher@0.6.0", "--transport", "stdio"], {"PATH": "/bin"}
    )
    assert cmd == ["/bin/uvx", "mcp-neo4j-cypher@0.6.0", "--transport", "stdio"]
    assert "s3cret" not in " ".join(cmd)
    assert env == {
        "PATH": "/bin",
        "NEO4J_URI": SETTINGS.uri,
        "NEO4J_USERNAME": "neo4j",
        "NEO4J_PASSWORD": "s3cret",
        "NEO4J_DATABASE": "abc123",
    }


def test_build_command_defaults(uvx):
    cmd, _ = cs.build_command(SETTINGS, [], {})
    assert cmd == ["/bin/uvx", cs.DEFAULT_SPEC, "--transport", "stdio"]
    cmd, _ = cs.build_command(SETTINGS, ["--read-only"], {})
    assert cmd == ["/bin/uvx", cs.DEFAULT_SPEC, "--read-only"]


def test_falls_back_to_uv_tool_run(monkeypatch):
    monkeypatch.setattr(cs.shutil, "which", lambda name: "/bin/uv" if name == "uv" else None)
    cmd, _ = cs.build_command(SETTINGS, [], {})
    assert cmd[:3] == ["/bin/uv", "tool", "run"]


def test_main_execs_with_settings(monkeypatch, uvx):
    monkeypatch.setattr(cs, "load_settings", lambda: SETTINGS)
    monkeypatch.setattr(cs.os, "name", "posix")
    seen = {}

    def fake_execve(path, args, env):
        seen.update(path=path, args=args, env=env)
        raise SystemExit(0)

    monkeypatch.setattr(cs.os, "execve", fake_execve)
    with pytest.raises(SystemExit):
        cs.main(["mcp-neo4j-cypher@0.6.0", "--transport", "stdio"])
    assert seen["path"] == "/bin/uvx"
    assert seen["env"]["NEO4J_DATABASE"] == "abc123"


def test_main_reports_missing_settings_on_stderr(monkeypatch, capsys):
    def fail():
        raise ConfigError("Missing Neo4j settings: NEO4J_URI")

    monkeypatch.setattr(cs, "load_settings", fail)
    assert cs.main([]) == 1
    out = capsys.readouterr()
    assert out.out == "" and "NEO4J_URI" in out.err
