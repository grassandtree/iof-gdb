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


@pytest.fixture
def cypher_exe(tmp_path):
    exe = tmp_path / "mcp-neo4j-cypher.exe"
    exe.write_text("")
    return str(exe)


def test_exe_var_wins_over_uvx_and_drops_package_spec(uvx, cypher_exe):
    base = {cs.EXE_VAR: cypher_exe}
    cmd, env = cs.build_command(SETTINGS, ["mcp-neo4j-cypher@0.6.0", "--read-only"], base)
    assert cmd == [cypher_exe, "--read-only"]
    assert env["NEO4J_PASSWORD"] == "s3cret"
    assert "s3cret" not in " ".join(cmd)
    cmd, _ = cs.build_command(SETTINGS, [], base)
    assert cmd == [cypher_exe, *cs.DEFAULT_ARGS]
    cmd, _ = cs.build_command(SETTINGS, ["--read-only"], base)
    assert cmd == [cypher_exe, "--read-only"]


def test_exe_var_pointing_to_missing_file(uvx, tmp_path):
    missing = str(tmp_path / "nope.exe")
    with pytest.raises(FileNotFoundError, match=cs.EXE_VAR):
        cs.build_command(SETTINGS, [], {cs.EXE_VAR: missing})


def test_no_uv_and_no_exe_var_mentions_exe_var(monkeypatch):
    monkeypatch.setattr(cs.shutil, "which", lambda name: None)
    with pytest.raises(FileNotFoundError, match=cs.EXE_VAR):
        cs.build_command(SETTINGS, [], {})


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
