"""Knowledge (instance) input.

Individuals and relations are turned into rdflib triples and written through the same
rdflib-neo4j import path as files, so everything ends up in one n10s-style graph.
Classes and properties are checked against the database first, so typos do not silently
create new vocabulary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from neo4j import Driver
from rdflib import OWL, RDF, RDFS, Graph, Literal, URIRef

from . import db
from .config import Settings, create_driver, load_settings
from .ontology import resource_kinds
from .prefixes import expand
from .profile import GraphNames, names_for, prefix_map, profile_for
from .rdf_import import import_graph

# Annotation properties that may be used without being declared in the database.
BUILTIN_LITERAL_PROPERTIES = {
    str(RDFS.label),
    str(RDFS.comment),
    "http://www.w3.org/2004/02/skos/core#prefLabel",
    "http://www.w3.org/2004/02/skos/core#altLabel",
    "http://www.w3.org/2004/02/skos/core#definition",
    "http://www.w3.org/2004/02/skos/core#note",
    "http://purl.org/dc/terms/description",
    "http://purl.org/dc/terms/identifier",
    "http://purl.org/dc/elements/1.1/identifier",
}


class KnowledgeError(ValueError):
    """Input that does not fit the ontology in the database."""


@dataclass
class Vocabulary:
    """What the database knows about the IRIs used in a request."""

    prefixes: dict[str, str]
    names: GraphNames
    kinds: dict[str, list[str] | None] = field(default_factory=dict)  # iri -> labels or None
    literal_properties: frozenset[str] = frozenset(BUILTIN_LITERAL_PROPERTIES)

    def expand(self, value: str) -> str:
        try:
            return expand(value, self.prefixes)
        except ValueError as e:
            raise KnowledgeError(str(e)) from None

    def property_kind(self, iri: str) -> str | None:
        """'object' / 'datatype' / 'annotation' for a property, None if unknown."""
        labels = self.kinds.get(iri)
        if labels:
            for label, kind in self.names.property_kinds.items():
                if label in labels:
                    return kind
        if iri in self.literal_properties:
            return "annotation"
        return None

    def is_class(self, iri: str) -> bool:
        return any(label in (self.kinds.get(iri) or []) for label in self.names.class_labels)


def _literal(value: Any) -> Literal:
    if isinstance(value, Mapping):
        datatype = value.get("datatype")
        return Literal(
            value["value"], lang=value.get("lang"), datatype=datatype and URIRef(datatype)
        )
    return Literal(value)


def build_individual(
    iri: str,
    class_iri: str,
    label: str | None,
    properties: Mapping[str, Any] | None,
    vocab: Vocabulary,
) -> Graph:
    """Triples for a new individual. Raises KnowledgeError if the vocabulary does not fit.

    ``properties`` maps a property (IRI or CURIE) to a value or a list of values. Object
    property values are IRIs / CURIEs of existing resources; other values become literals
    (``{"value": ..., "lang": "en"}`` or ``{"value": ..., "datatype": xsd IRI}`` also work).
    """
    subject = URIRef(vocab.expand(iri))
    cls = vocab.expand(class_iri)
    labels = vocab.kinds.get(cls)
    if labels is None:
        raise KnowledgeError(f"Class {cls} is not in the database. Use search_classes to find it.")
    if not vocab.is_class(cls):
        raise KnowledgeError(
            f"{cls} exists but is not a class (labels: {labels}; "
            f"class labels in this profile: {list(vocab.names.class_labels)})."
        )

    g = Graph()
    g.add((subject, RDF.type, OWL.NamedIndividual))
    g.add((subject, RDF.type, URIRef(cls)))
    if label:
        g.add((subject, RDFS.label, Literal(label)))
    for prop, values in (properties or {}).items():
        p = vocab.expand(prop)
        kind = vocab.property_kind(p)
        if kind is None:
            raise KnowledgeError(
                f"Property {p} is not in the database. Use search_properties to find it."
            )
        for value in values if isinstance(values, list) else [values]:
            if kind == "object":
                if not isinstance(value, str):
                    raise KnowledgeError(f"{p} is an object property; give an IRI, not {value!r}.")
                target = vocab.expand(value)
                if vocab.kinds.get(target) is None:
                    raise KnowledgeError(
                        f"Object {target} of {p} is not in the database; add it first."
                    )
                g.add((subject, URIRef(p), URIRef(target)))
            else:
                g.add((subject, URIRef(p), _literal(value)))
    return g


def build_relation(subject: str, prop: str, obj: str, vocab: Vocabulary) -> Graph:
    s, p, o = vocab.expand(subject), vocab.expand(prop), vocab.expand(obj)
    kind = vocab.property_kind(p)
    if kind is None:
        raise KnowledgeError(f"Property {p} is not in the database. Use search_properties.")
    if kind != "object":
        raise KnowledgeError(f"{p} is a {kind} property; relations need an object property.")
    for iri, role in ((s, "Subject"), (o, "Object")):
        if vocab.kinds.get(iri) is None:
            raise KnowledgeError(f"{role} {iri} is not in the database; add it first.")
    g = Graph()
    g.add((URIRef(s), URIRef(p), URIRef(o)))
    return g


def lookup_vocabulary(
    driver: Driver, settings: Settings, strict: Sequence[str], loose: Sequence[str] = ()
) -> Vocabulary:
    """Look up the database labels of IRIs / CURIEs.

    ``strict`` values must expand (unknown prefix -> KnowledgeError); ``loose`` values are
    property values that may just be text, so the ones that do not expand are skipped.
    """
    profile = profile_for(settings)
    vocab = Vocabulary(
        prefixes=prefix_map(settings, db.load_prefix_defs(driver)),
        names=names_for(settings.profile),
        literal_properties=frozenset(
            BUILTIN_LITERAL_PROPERTIES
            | set(profile.label_properties)
            | set(profile.synonym_properties)
            | set(profile.definition_properties)
        ),
    )
    iris = [vocab.expand(v) for v in strict]
    for value in loose:
        if " " in value.strip() or ":" not in value:
            continue
        try:
            iri = expand(value, vocab.prefixes)
        except ValueError:
            continue
        if iri != value or "://" in value or value.startswith("urn:"):
            iris.append(iri)
    for iri in dict.fromkeys(iris):
        vocab.kinds[iri] = resource_kinds(driver, iri)
    return vocab


def _string_values(properties: Mapping[str, Any] | None) -> list[str]:
    out: list[str] = []
    for values in (properties or {}).values():
        for v in values if isinstance(values, list) else [values]:
            if isinstance(v, str):
                out.append(v)
    return out


def add_individual(
    iri: str,
    class_iri: str,
    label: str | None = None,
    properties: Mapping[str, Any] | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Create (or extend) an individual of an existing class, then import it."""
    settings = settings or load_settings()
    driver = create_driver(settings)
    try:
        vocab = lookup_vocabulary(
            driver, settings, [iri, class_iri, *(properties or {})], _string_values(properties)
        )
        graph = build_individual(iri, class_iri, label, properties, vocab)
    finally:
        driver.close()
    subject = vocab.expand(iri)
    triples, _ = import_graph(graph, settings)
    return {
        "iri": subject,
        "class": vocab.expand(class_iri),
        "triples": triples,
        "status": "updated" if vocab.kinds.get(subject) is not None else "created",
    }


def add_relation(
    subject: str, prop: str, obj: str, settings: Settings | None = None
) -> dict[str, Any]:
    """Link two existing resources with an existing object property, then import it."""
    settings = settings or load_settings()
    driver = create_driver(settings)
    try:
        vocab = lookup_vocabulary(driver, settings, [subject, prop, obj])
        graph = build_relation(subject, prop, obj, vocab)
    finally:
        driver.close()
    triples, _ = import_graph(graph, settings)
    s, p, o = (vocab.expand(x) for x in (subject, prop, obj))
    return {"subject": s, "property": p, "object": o, "triples": triples}
