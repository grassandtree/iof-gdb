"""Namespace prefixes used by the SHORTEN strategy, plus CURIE helpers.

With HANDLE_VOCAB_URI_STRATEGY.SHORTEN, rdflib-neo4j turns every predicate IRI and every
rdf:type object IRI into ``prefix__localName`` (e.g. ``rdfs__label``, ``owl__Class``).
Its namespace must have a prefix, or the import fails with ShortenStrictException.

The W3C prefixes below are always present. Domain prefixes (IOF, BFO, ...) come from the
active profile (see profile.py), auto-generated ones from the database.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from rdflib import RDF, Graph, URIRef
from rdflib_neo4j.utils import getLocalPart, getNamespacePart

W3C_PREFIXES: dict[str, str] = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
}

INSTANCE_PREFIX = "inst"

_PREFIX_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
_CURIE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):([^/].*)?$")


def build_prefixes(
    instance_ns: str | None = None,
    extra: Mapping[str, str] | None = None,
    domain: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """W3C + domain (profile) + instance namespace + extra (e.g. stored/auto) prefixes.

    Earlier sources win on name clashes; ``extra`` only adds names that are still free.
    """
    prefixes = dict(W3C_PREFIXES)
    for name, ns in (domain or {}).items():
        prefixes.setdefault(name, ns)
    if instance_ns:
        prefixes.setdefault(INSTANCE_PREFIX, instance_ns)
    for name, ns in (extra or {}).items():
        prefixes.setdefault(name, ns)
    return prefixes


def namespace_of(iri: str) -> str:
    """Namespace part exactly as rdflib-neo4j computes it (up to the last '#', '/' or ':')."""
    return getNamespacePart(str(iri))


def vocab_namespaces(graph: Graph) -> set[str]:
    """Namespaces that SHORTEN must resolve: all predicates and all IRI objects of rdf:type."""
    namespaces: set[str] = set()
    for _, p, o in graph:
        namespaces.add(namespace_of(p))
        if p == RDF.type and isinstance(o, URIRef):
            namespaces.add(namespace_of(o))
    return namespaces


def find_unregistered(graph: Graph, prefixes: Mapping[str, str]) -> set[str]:
    """Namespaces used as vocabulary in ``graph`` that have no prefix."""
    known = set(prefixes.values())
    return {ns for ns in vocab_namespaces(graph) if ns not in known}


def _candidate_name(ns: str) -> str:
    parts = [p for p in re.split(r"[/#:]+", ns) if p]
    for part in reversed(parts):
        name = re.sub(r"[^A-Za-z0-9]", "", part).lower()
        if name and name[0].isalpha() and name not in {"http", "https", "www", "ontology"}:
            return name[:20]
    return "ns"


def generate_prefixes(namespaces: Iterable[str], existing: Mapping[str, str]) -> dict[str, str]:
    """Make up prefix names for unregistered namespaces (deterministic for a given input)."""
    taken = set(existing)
    known_ns = set(existing.values())
    new: dict[str, str] = {}
    for ns in sorted(set(namespaces) - known_ns):
        base = _candidate_name(ns)
        name, i = base, 0
        while name in taken:
            name = f"{base}{i}"
            i += 1
        taken.add(name)
        new[name] = ns
    return new


def is_valid_prefix_name(name: str) -> bool:
    return bool(_PREFIX_NAME.match(name))


def expand(iri_or_curie: str, prefixes: Mapping[str, str]) -> str:
    """Expand ``prefix:local`` (or ``prefix__local``) to a full IRI; full IRIs pass through."""
    value = iri_or_curie.strip()
    if value.startswith("<") and value.endswith(">"):
        return value[1:-1]
    if "__" in value and "://" not in value:
        prefix, _, local = value.partition("__")
        if prefix in prefixes:
            return prefixes[prefix] + local
    match = _CURIE.match(value)
    if match and match.group(1) in prefixes:
        return prefixes[match.group(1)] + (match.group(2) or "")
    if match and "://" not in value and not value.startswith("urn:"):
        raise ValueError(f"Unknown prefix {match.group(1)!r} in {value!r}")
    return value


def shorten(iri: str, prefixes: Mapping[str, str]) -> str | None:
    """IRI -> the ``prefix__local`` name rdflib-neo4j stores for it (None if unregistered)."""
    by_ns = {ns: name for name, ns in prefixes.items()}
    name = by_ns.get(namespace_of(iri))
    return f"{name}__{getLocalPart(str(iri))}" if name else None


def to_turtle_header(prefixes: Mapping[str, str]) -> str:
    return "".join(f"@prefix {name}: <{ns}> .\n" for name, ns in sorted(prefixes.items()))
