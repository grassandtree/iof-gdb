"""Command line interface (typer)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from neo4j.exceptions import AuthError, ServiceUnavailable

from . import db, rdf_import
from .config import ONTOLOGY_DIR, ConfigError, create_driver, load_settings

IOF_CORE_IRI = "https://spec.industrialontologies.org/ontology/core/Core/"

app = typer.Typer(
    help="Load IOF Core / BFO ontologies and instance data into Neo4j Aura.",
    no_args_is_help=True,
    add_completion=False,
)


def _echo_json(data: Any) -> None:
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _fail(message: str) -> typer.Exit:
    typer.secho(message, fg=typer.colors.RED, err=True)
    return typer.Exit(code=1)


def _run(fn, *args, **kwargs):
    """Call ``fn`` and turn expected errors into a short message + exit code 1."""
    try:
        return fn(*args, **kwargs)
    except ConfigError as e:
        raise _fail(str(e)) from None
    except AuthError as e:
        raise _fail(f"Neo4j authentication failed: {e.message}") from None
    except ServiceUnavailable as e:
        raise _fail(f"Neo4j is not reachable: {e}") from None
    except rdf_import.UnregisteredNamespaceError as e:
        raise _fail(str(e)) from None


def _with_driver(fn):
    settings = load_settings()
    driver = create_driver(settings)
    try:
        return fn(driver)
    finally:
        driver.close()


@app.command()
def init() -> None:
    """Check the connection and create the :Resource(uri) uniqueness constraint."""
    _echo_json(_run(_with_driver, db.init))


@app.command("import")
def import_cmd(
    source: Annotated[str, typer.Argument(help="File path or URL of an RDF document.")],
    format: Annotated[
        str | None,
        typer.Option("--format", "-f", help="turtle/ttl, xml (RDF/XML), nt, n3, json-ld."),
    ] = None,
    follow_imports: Annotated[
        bool, typer.Option("--follow-imports", help="Also load the owl:imports closure.")
    ] = False,
    auto_prefix: Annotated[
        bool,
        typer.Option("--auto-prefix", help="Generate prefixes for unregistered namespaces."),
    ] = False,
) -> None:
    """Import an RDF/OWL file or URL into Neo4j."""
    summary = _run(
        rdf_import.import_source,
        source,
        format,
        follow_imports,
        auto_prefix=auto_prefix,
    )
    _echo_json(summary.as_dict())


@app.command()
def stats() -> None:
    """Show node / relationship counts per label and type."""
    _echo_json(_run(_with_driver, db.stats))


@app.command("fetch-iof")
def fetch_iof(
    iris: Annotated[
        list[str] | None,
        typer.Argument(help="Ontology IRIs to download (default: IOF Core)."),
    ] = None,
    dest: Annotated[
        Path, typer.Option("--dest", help="Target directory.", file_okay=False)
    ] = ONTOLOGY_DIR,
    follow_imports: Annotated[
        bool,
        typer.Option(
            "--follow-imports/--no-follow-imports", help="Also download owl:imports (BFO, ...)."
        ),
    ] = True,
) -> None:
    """Download IOF Core and its imports (BFO, IOF annotation vocabulary) for local import."""
    saved = rdf_import.fetch_ontologies(iris or [IOF_CORE_IRI], dest, follow_imports)
    _echo_json({iri: str(path) for iri, path in saved.items()})
    typer.echo(f"Catalog: {dest / rdf_import.CATALOG_FILE}", err=True)
