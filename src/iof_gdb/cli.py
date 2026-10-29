"""Command line interface (typer)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from neo4j.exceptions import AuthError, ServiceUnavailable

from . import db, rdf_import
from .config import ONTOLOGY_DIR, ConfigError, create_driver, load_settings
from .profile import GraphNames, ProfileError, active_profile_name, builtin_profiles, load_profile
from .storage import MultiValueError

app = typer.Typer(
    help="Load RDF/OWL ontologies and instance data into Neo4j (profile: IOF_GDB_PROFILE).",
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
    except (rdf_import.UnregisteredNamespaceError, MultiValueError, ProfileError) as e:
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
def reset(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
) -> None:
    """Delete ALL nodes and relationships in the database (constraints are kept)."""

    settings = _run(load_settings)
    driver = create_driver(settings)
    try:
        before = _run(db.stats, driver)
        typer.echo(
            f"Database {settings.database!r} at {settings.uri}: "
            f"{before['nodes']} nodes, {before['relationships']} relationships.",
            err=True,
        )
        confirmed = yes or not before["nodes"]
        if not confirmed and not typer.confirm("Delete everything?", default=False, err=True):
            raise typer.Abort()
        _echo_json({"deleted_nodes": _run(db.reset, driver) if before["nodes"] else 0})
    finally:
        driver.close()


@app.command()
def stats() -> None:
    """Show node / relationship counts per label and type."""
    _echo_json(_run(_with_driver, db.stats))


def _fetch(iris: list[str] | None, dest: Path, follow_imports: bool) -> None:
    profile = _run(load_profile, active_profile_name())
    targets = iris or list(profile.fetch_iris)
    if not targets:
        raise _fail(f"Profile {profile.name!r} has no [fetch] default_iris; give IRIs to fetch.")
    saved = rdf_import.fetch_ontologies(targets, dest, follow_imports)
    _echo_json({iri: str(path) for iri, path in saved.items()})
    typer.echo(f"Catalog: {dest / rdf_import.CATALOG_FILE}", err=True)


IrisArg = Annotated[
    list[str] | None,
    typer.Argument(help="Ontology IRIs to download (default: the profile's [fetch] list)."),
]
DestOpt = Annotated[Path, typer.Option("--dest", help="Target directory.", file_okay=False)]
FollowOpt = Annotated[
    bool,
    typer.Option("--follow-imports/--no-follow-imports", help="Also download owl:imports."),
]


@app.command()
def fetch(
    iris: IrisArg = None, dest: DestOpt = ONTOLOGY_DIR, follow_imports: FollowOpt = True
) -> None:
    """Download ontologies (and their imports) into data/ontologies for local import."""
    _fetch(iris, dest, follow_imports)


@app.command("fetch-iof")
def fetch_iof(
    iris: IrisArg = None, dest: DestOpt = ONTOLOGY_DIR, follow_imports: FollowOpt = True
) -> None:
    """Same as `fetch` (kept for compatibility; with the iof profile: IOF Core + BFO)."""
    _fetch(iris, dest, follow_imports)


@app.command()
def profile(
    name: Annotated[
        str | None,
        typer.Argument(help="Built-in profile name or .toml path (default: IOF_GDB_PROFILE)."),
    ] = None,
) -> None:
    """Show (and validate) a profile: prefixes, class/label/definition names, fetch list."""
    p = _run(load_profile, name or active_profile_name())
    names = GraphNames.from_profile(p)
    _echo_json(
        {
            "name": p.name,
            "description": p.description,
            "source": p.source,
            "builtin_profiles": builtin_profiles(),
            "prefixes": p.prefixes,
            "class_labels": names.class_labels,
            "label_properties": names.label_props,
            "synonym_properties": names.synonym_props,
            "definition_properties": names.definition_props,
            "languages": p.languages,
            "multivalued": p.multivalued,
            "fetch_iris": p.fetch_iris,
        }
    )
