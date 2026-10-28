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


def test_fetch_iof_defaults_to_core(monkeypatch, tmp_path):
    seen = {}

    def fake_fetch(iris, dest, follow_imports):
        seen.update(iris=iris, dest=dest, follow=follow_imports)
        return {iris[0]: dest / "Core.rdf"}

    monkeypatch.setattr(rdf_import, "fetch_ontologies", fake_fetch)
    result = runner.invoke(cli.app, ["fetch-iof", "--dest", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert seen == {"iris": [cli.IOF_CORE_IRI], "dest": tmp_path, "follow": True}
