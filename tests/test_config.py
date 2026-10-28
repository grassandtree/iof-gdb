import pytest
from rdflib_neo4j import HANDLE_VOCAB_URI_STRATEGY

from iof_gdb import config
from iof_gdb.prefixes import BASE_PREFIXES


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for key in (
        "NEO4J_URI",
        "NEO4J_USERNAME",
        "NEO4J_PASSWORD",
        "NEO4J_DATABASE",
        "IOF_GDB_INSTANCE_NS",
    ):
        # setenv first so teardown restores the original state even for keys
        # that load_dotenv() sets during the test
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)
    return tmp_path / "missing.env"


def test_missing_settings_raise(clean_env):
    with pytest.raises(config.ConfigError, match="NEO4J_URI"):
        config.load_settings(clean_env)


def test_settings_from_env_file(clean_env, tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "NEO4J_URI=neo4j+s://x.databases.neo4j.io\nNEO4J_USERNAME=u\nNEO4J_PASSWORD=s3cret-pw\n"
    )
    s = config.load_settings(env)
    assert s.database == "neo4j"
    assert s.auth_data() == {
        "uri": "neo4j+s://x.databases.neo4j.io",
        "database": "neo4j",
        "user": "u",
        "pwd": "s3cret-pw",
    }
    assert "s3cret-pw" not in repr(s)


def test_store_config(clean_env, monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "neo4j://localhost:7687")
    monkeypatch.setenv("NEO4J_USERNAME", "u")
    monkeypatch.setenv("NEO4J_PASSWORD", "p")
    monkeypatch.setenv("NEO4J_DATABASE", "db1")
    s = config.load_settings(clean_env)
    cfg = config.create_store_config(s, BASE_PREFIXES)
    assert cfg.handle_vocab_uri_strategy is HANDLE_VOCAB_URI_STRATEGY.SHORTEN
    assert cfg.batching is True
    assert cfg.auth_data["database"] == "db1"
    assert cfg.get_prefixes()["iofav"] == BASE_PREFIXES["iofav"]
    # the config must not share the caller's dict
    assert cfg.custom_prefixes is not BASE_PREFIXES
