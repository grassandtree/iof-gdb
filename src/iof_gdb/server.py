"""MCP server (stdio) for RDF import, ontology exploration and knowledge input.

General Cypher reads/writes and schema inspection are left to the official
``mcp-neo4j-cypher`` server; this one only adds RDF / ontology specific tools.
"""

from __future__ import annotations

import argparse
import functools
import json
import logging
import os
import sys
from collections.abc import Callable
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from neo4j import Driver
from neo4j.exceptions import AuthError, ClientError, ServiceUnavailable
from rdflib.exceptions import ParserError

from . import db, http_app, knowledge, ontology, rdf_import
from .config import (
    DEFAULT_INSTANCE_NS,
    ConfigError,
    Settings,
    create_driver,
    load_settings,
)
from .http_app import TOKEN_ENV
from .prefixes import build_prefixes, expand, shorten
from .profile import (
    GraphNames,
    Profile,
    ProfileError,
    active_profile_name,
    load_profile,
    names_for,
    prefix_map,
)

GENERIC_INSTRUCTIONS = """\
RDF/OWL tools for a Neo4j graph loaded with rdflib-neo4j (n10s-style). Every node is
:Resource with a unique `uri`; vocabulary is stored as prefix__localName (class label
{class_labels}, label property {label_prop}, relationship {sub_class_of}). IRIs can be given
in full or as CURIEs; see the iof://prefixes and iof://profile resources.
Language-tagged text is also stored per language as `property@lang` (languages: {languages}),
e.g. n.`{label_prop}@ko` (backticks needed); {label_prop} itself holds the untagged or
{primary} value. Multivalued properties ({multivalued}) are lists.
For class hierarchies prefer the derived (:Resource)-[:SUBCLASS_OF]->(:Resource): named classes
only, `inferred: true` when implied by an owl:intersectionOf definition. {sub_class_of} also
points to restriction blank nodes; blank nodes carry the :BlankNode label.
To add knowledge: search_classes / search_properties first, then add_individual and
add_relation (they refuse unknown classes and properties), or import_rdf_text for Turtle.
Use the neo4j-cypher server for free-form Cypher.
"""


def build_instructions(profile: Profile, names: GraphNames) -> str:
    prefixes = build_prefixes(domain=profile.prefixes)
    multivalued = [shorten(p, prefixes) or p for p in profile.multivalued]
    text = GENERIC_INSTRUCTIONS.format(
        class_labels=" / ".join(names.class_labels),
        label_prop=names.label_props[0],
        sub_class_of=names.sub_class_of,
        languages=", ".join(profile.languages) or "none",
        primary=profile.languages[0] if profile.languages else "first",
        multivalued=", ".join(multivalued) or "none",
    )
    return text + ("\n" + profile.instructions + "\n" if profile.instructions else "")


# The profile shapes the server instructions, so it is read at start-up (.env included).
PROFILE = load_profile(active_profile_name())

logger = logging.getLogger("iof_gdb.server")
mcp = MCPServer(
    "iof-gdb", instructions=build_instructions(PROFILE, GraphNames.from_profile(PROFILE))
)

_state: dict[str, Any] = {}
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True)


def _settings() -> Settings:
    if "settings" not in _state:
        _state["settings"] = load_settings()
    return _state["settings"]


def _driver() -> Driver:
    if "driver" not in _state:
        _state["driver"] = create_driver(_settings())
    return _state["driver"]


def _prefixes() -> dict[str, str]:
    return prefix_map(_settings(), db.load_prefix_defs(_driver()))


def _names() -> GraphNames:
    return names_for(_settings().profile)


def _iri(value: str) -> str:
    return expand(value, _prefixes())


def tool_errors[F: Callable[..., Any]](fn: F) -> F:
    """Turn expected failures into ToolError so the model sees the message."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except ToolError:
            raise
        except (
            ConfigError,
            ProfileError,
            ValueError,
            FileNotFoundError,
            ParserError,
            SyntaxError,
        ) as e:
            raise ToolError(str(e)) from e
        except AuthError as e:
            raise ToolError(f"Neo4j authentication failed: {e.message}") from e
        except ServiceUnavailable as e:
            raise ToolError(f"Neo4j is not reachable: {e}") from e
        except ClientError as e:
            raise ToolError(f"Neo4j error {e.code}: {e.message}") from e
        except OSError as e:
            raise ToolError(f"{type(e).__name__}: {e}") from e

    return wrapper  # type: ignore[return-value]


@mcp.tool(annotations=WRITES)
@tool_errors
def import_rdf(
    source: str,
    format: str | None = None,
    follow_imports: bool = False,
    auto_prefix: bool = False,
) -> dict[str, Any]:
    """Import an RDF/OWL document from a file path or URL.

    format: turtle, xml (RDF/XML, also .owl), nt, n3 or json-ld; guessed when omitted.
    follow_imports: also load the owl:imports closure (local copies in data/ontologies first).
    auto_prefix: invent prefixes for namespaces that are not registered (otherwise it fails).
    """
    return rdf_import.import_source(
        source, format, follow_imports, auto_prefix=auto_prefix, settings=_settings()
    ).as_dict()


@mcp.tool(annotations=WRITES)
@tool_errors
def import_rdf_text(
    content: str, format: str = "turtle", auto_prefix: bool = False
) -> dict[str, Any]:
    """Import an RDF snippet you wrote (default Turtle). Declare every @prefix you use.

    This is the main way to add knowledge in bulk; for single facts add_individual and
    add_relation also check the classes and properties first.
    """
    return rdf_import.import_text(
        content, format, auto_prefix=auto_prefix, settings=_settings()
    ).as_dict()


@mcp.tool(annotations=READ_ONLY)
@tool_errors
def search_classes(term: str, limit: int = 20) -> list[dict[str, Any]]:
    """Find classes whose label, synonym or IRI contains `term` (case-insensitive)."""
    return ontology.search_classes(_driver(), term, limit, names=_names())


@mcp.tool(annotations=READ_ONLY)
@tool_errors
def describe_class(iri_or_curie: str) -> dict[str, Any]:
    """Label, definition, annotations, super/sub classes, restrictions and the properties
    whose domain or range is the class."""
    iri = _iri(iri_or_curie)
    result = ontology.describe_class(_driver(), iri, names=_names())
    if result is None:
        raise ToolError(f"{iri} is not in the database. Try search_classes.")
    return result


@mcp.tool(annotations=READ_ONLY)
@tool_errors
def search_properties(
    term: str, limit: int = 20, kind: Literal["object", "datatype"] | None = None
) -> list[dict[str, Any]]:
    """Find object / datatype properties whose label or IRI contains `term`, with their
    domain and range."""
    return ontology.search_properties(_driver(), term, limit, kind, names=_names())


@mcp.tool(annotations=WRITES)
@tool_errors
def add_individual(
    iri: str,
    class_iri: str,
    label: str | None = None,
    properties: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create an individual (owl:NamedIndividual) of a class that exists in the database.

    iri: IRI or CURIE for the new individual, e.g. inst:P-101.
    class_iri: IRI or CURIE of a class that is already in the DB (see search_classes).
    label: plain (untagged) label. For labels in a language use properties, e.g.
    {"rdfs:label": {"value": "펌프", "lang": "ko"}}.
    properties: {property IRI/CURIE: value or [values]}. Object property values are IRIs of
    existing resources; data/annotation values are literals ({"value": "x", "lang": "en"} or
    {"value": "5", "datatype": "http://www.w3.org/2001/XMLSchema#integer"} also work).
    """
    return knowledge.add_individual(iri, class_iri, label, properties, settings=_settings())


@mcp.tool(annotations=WRITES)
@tool_errors
def add_relation(subject: str, property: str, object: str) -> dict[str, Any]:
    """Link two existing resources with an object property that exists in the database,
    e.g. (inst:P-101, <property CURIE>, inst:P-101-role)."""
    return knowledge.add_relation(subject, property, object, settings=_settings())


@mcp.tool(annotations=READ_ONLY)
@tool_errors
def graph_stats() -> dict[str, Any]:
    """Node and relationship counts, per label and per relationship type."""
    return db.stats(_driver())


@mcp.resource(
    "iof://prefixes",
    name="prefixes",
    description="Prefix -> namespace map used for CURIEs and for stored names (prefix__local).",
    mime_type="application/json",
)
def prefixes_resource() -> str:
    try:
        prefixes = _prefixes()
    except Exception as e:  # noqa: BLE001 - fall back to the profile's list without a DB
        logger.warning("Using profile prefixes only: %s", e)
        settings = _state.get("settings")
        prefixes = build_prefixes(
            settings.instance_ns
            if settings
            else os.getenv("IOF_GDB_INSTANCE_NS", DEFAULT_INSTANCE_NS),
            domain=PROFILE.prefixes,
        )
    return json.dumps(prefixes, indent=2)


@mcp.resource(
    "iof://profile",
    name="profile",
    description="Active profile: what counts as a class, label and definition, as stored names.",
    mime_type="application/json",
)
def profile_resource() -> str:
    names = GraphNames.from_profile(PROFILE)
    return json.dumps(
        {
            "name": PROFILE.name,
            "description": PROFILE.description,
            "class_labels": names.class_labels,
            "label_properties": names.label_props,
            "synonym_properties": names.synonym_props,
            "definition_properties": names.definition_props,
            "languages": PROFILE.languages,
            "multivalued": PROFILE.multivalued,
        },
        indent=2,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="iof-gdb-mcp",
        description="iof-gdb MCP server. stdio for Claude Code; streamable-http for Open WebUI "
        f"and other HTTP clients (optional bearer token via ${TOKEN_ENV}).",
    )
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind address")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port")
    parser.add_argument("--path", default="/mcp", help="HTTP endpoint path")
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        metavar="HOST[:PORT]",
        help="extra Host header to accept besides localhost (repeatable), e.g. "
        "host.docker.internal; '*' disables the check",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    # stdout carries the MCP protocol in stdio mode; all logging goes to stderr.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    try:
        if args.transport == "stdio":
            mcp.run("stdio")
        else:
            token = os.getenv(TOKEN_ENV) or None
            if not token and args.host not in ("127.0.0.1", "localhost", "::1"):
                logger.warning(
                    "Serving on %s without %s: anyone who can reach it can write to the database.",
                    args.host,
                    TOKEN_ENV,
                )
            http_app.serve(
                mcp,
                host=args.host,
                port=args.port,
                path=args.path,
                allowed_hosts=args.allowed_host,
                token=token,
            )
    finally:
        driver = _state.pop("driver", None)
        if driver is not None:
            driver.close()


if __name__ == "__main__":
    main()
