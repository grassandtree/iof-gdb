import json

from typer.testing import CliRunner

from iof_gdb import cli, rdf_import

runner = CliRunner()


def test_help_lists_commands():
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("init", "import", "stats", "fetch-iof"):
        assert cmd in result.output


def test_import_passes_options(monkeypatch):
    seen = {}

    def fake_import(source, fmt, follow_imports, *, auto_prefix):
        seen.update(source=source, fmt=fmt, follow=follow_imports, auto=auto_prefix)
        return rdf_import.ImportSummary(triples=3, ontologies=[], sources=[source], seconds=0.1)

    monkeypatch.setattr(rdf_import, "import_source", fake_import)
    result = runner.invoke(
        cli.app, ["import", "x.owl", "--format", "xml", "--follow-imports", "--auto-prefix"]
    )
    assert result.exit_code == 0, result.output
    assert seen == {"source": "x.owl", "fmt": "xml", "follow": True, "auto": True}
    assert json.loads(result.stdout)["triples"] == 3


def test_import_reports_unregistered_namespaces(monkeypatch):
    def fake_import(*args, **kwargs):
        raise rdf_import.UnregisteredNamespaceError(["https://new.example/"])

    monkeypatch.setattr(rdf_import, "import_source", fake_import)
    result = runner.invoke(cli.app, ["import", "x.ttl"])
    assert result.exit_code == 1
    assert "https://new.example/" in result.output


def test_missing_settings_is_a_clean_error(monkeypatch, tmp_path):
    def fail():
        raise cli.ConfigError("Missing Neo4j settings: NEO4J_URI")

    monkeypatch.setattr(cli, "load_settings", fail)
    result = runner.invoke(cli.app, ["stats"])
    assert result.exit_code == 1
    assert "NEO4J_URI" in result.output


def test_fetch_defaults_to_profile_list(monkeypatch, tmp_path):
    seen = {}

    def fake_fetch(iris, dest, follow_imports):
        seen.update(iris=iris, dest=dest, follow=follow_imports)
        return {iris[0]: dest / "Core.rdf"}

    monkeypatch.setattr(rdf_import, "fetch_ontologies", fake_fetch)
    monkeypatch.setenv("IOF_GDB_PROFILE", "iof")
    for cmd in ("fetch", "fetch-iof"):
        result = runner.invoke(cli.app, [cmd, "--dest", str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert seen == {
            "iris": ["https://spec.industrialontologies.org/ontology/core/Core/"],
            "dest": tmp_path,
            "follow": True,
        }


def test_fetch_without_default_list_needs_iris(monkeypatch, tmp_path):
    monkeypatch.setenv("IOF_GDB_PROFILE", "generic")
    result = runner.invoke(cli.app, ["fetch", "--dest", str(tmp_path)])
    assert result.exit_code == 1
    assert "default_iris" in result.output


def test_profile_command(monkeypatch):
    monkeypatch.setenv("IOF_GDB_PROFILE", "iof")
    result = runner.invoke(cli.app, ["profile"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["name"] == "iof"
    assert "iofav__naturalLanguageDefinition" in data["definition_properties"]
    result = runner.invoke(cli.app, ["profile", "nope"])
    assert result.exit_code == 1 and "Unknown profile" in result.output


class _FakeDriver:
    def close(self):
        pass


def _fake_reset_env(monkeypatch, nodes):
    from iof_gdb import db
    from iof_gdb.config import Settings

    calls = []
    monkeypatch.setattr(cli, "load_settings", lambda: Settings("neo4j://x", "u", "p"))
    monkeypatch.setattr(cli, "create_driver", lambda s: _FakeDriver())
    monkeypatch.setattr(db, "stats", lambda d: {"nodes": nodes, "relationships": 0})
    monkeypatch.setattr(db, "reset", lambda d: calls.append("reset") or nodes)
    return calls


def test_reset_asks_before_deleting(monkeypatch):
    calls = _fake_reset_env(monkeypatch, 10)
    result = runner.invoke(cli.app, ["reset"], input="n\n")
    assert result.exit_code == 1 and calls == []
    result = runner.invoke(cli.app, ["reset"], input="y\n")
    assert result.exit_code == 0 and calls == ["reset"]
    out = result.stdout  # the test runner echoes the typed answer before the JSON
    assert json.loads(out[out.index("{") :])["deleted_nodes"] == 10


def test_reset_yes_skips_prompt(monkeypatch):
    calls = _fake_reset_env(monkeypatch, 3)
    result = runner.invoke(cli.app, ["reset", "--yes"])
    assert result.exit_code == 0 and calls == ["reset"]
