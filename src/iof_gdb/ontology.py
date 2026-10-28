"""Ontology exploration queries over the rdflib-neo4j (n10s-style) graph.

Naming follows the SHORTEN strategy: owl:Class -> label ``owl__Class``, rdfs:label ->
property ``rdfs__label``, rdfs:subClassOf -> relationship ``rdfs__subClassOf``. Blank nodes
(OWL restrictions, unionOf lists, ...) are stored as :Resource nodes whose ``uri`` is the
rdflib blank-node id, so "named" resources are filtered with ``uri CONTAINS ':'``.
"""

from __future__ import annotations

from typing import Any

from neo4j import Driver

from .db import read

# Literal properties shown as "definition", in order of preference.
DEFINITION_PROPS = ("iofav__naturalLanguageDefinition", "skos__definition", "rdfs__comment")
LABEL_PROPS = ("rdfs__label", "skos__prefLabel")
PROPERTY_KINDS = {
    "owl__ObjectProperty": "object",
    "owl__DatatypeProperty": "datatype",
    "owl__AnnotationProperty": "annotation",
}

_LABEL = "coalesce(n.rdfs__label, n.skos__prefLabel)"
_DEFINITION = "coalesce(n.iofav__naturalLanguageDefinition, n.skos__definition, n.rdfs__comment)"
_NAMED = "n.uri CONTAINS ':'"
_MATCHES_TERM = (
    "(toLower(coalesce(n.rdfs__label, '')) CONTAINS toLower($term) "
    "OR toLower(coalesce(n.skos__prefLabel, '')) CONTAINS toLower($term) "
    "OR toLower(coalesce(n.skos__altLabel, '')) CONTAINS toLower($term) "
    "OR toLower(n.uri) CONTAINS toLower($term))"
)
# exact label first, then shorter labels
_ORDER = (
    "ORDER BY CASE WHEN toLower(label) = toLower($term) THEN 0 ELSE 1 END, "
    "size(coalesce(label, iri)), iri"
)


def _named(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen, out = set(), []
    for item in items:
        iri = item.get("iri")
        if iri and ":" in iri and iri not in seen:
            seen.add(iri)
            out.append(item)
    return out


def search_classes(driver: Driver, term: str, limit: int = 20) -> list[dict[str, Any]]:
    """OWL classes whose label / IRI contains ``term`` (case-insensitive)."""
    return read(
        driver,
        f"MATCH (n:owl__Class) WHERE {_NAMED} AND {_MATCHES_TERM} "
        f"WITH n, n.uri AS iri, {_LABEL} AS label "
        f"RETURN iri, label, {_DEFINITION} AS definition {_ORDER} LIMIT $limit",
        term=term,
        limit=limit,
    )


def search_properties(
    driver: Driver, term: str, limit: int = 20, kind: str | None = None
) -> list[dict[str, Any]]:
    """Object / datatype properties whose label / IRI contains ``term``."""
    labels = ["owl__ObjectProperty", "owl__DatatypeProperty"]
    if kind:
        wanted = {v: k for k, v in PROPERTY_KINDS.items()}.get(kind)
        if wanted not in labels:
            raise ValueError("kind must be 'object' or 'datatype'")
        labels = [wanted]
    label_filter = " OR ".join(f"n:{label}" for label in labels)
    rows = read(
        driver,
        f"MATCH (n:Resource) WHERE ({label_filter}) AND {_NAMED} AND {_MATCHES_TERM} "
        f"WITH n, n.uri AS iri, {_LABEL} AS label "
        "OPTIONAL MATCH (n)-[:rdfs__domain]->(d) "
        "OPTIONAL MATCH (n)-[:rdfs__range]->(r) "
        "WITH n, iri, label, collect(DISTINCT d.uri) AS domain_iris, "
        "collect(DISTINCT r.uri) AS range_iris "
        f"RETURN iri, label, {_DEFINITION} AS definition, "
        "CASE WHEN n:owl__ObjectProperty THEN 'object' ELSE 'datatype' END AS kind, "
        f"domain_iris, range_iris {_ORDER} LIMIT $limit",
        term=term,
        limit=limit,
    )
    for row in rows:
        for key in ("domain", "range"):
            # blank node = class expression (e.g. owl:unionOf)
            row[key] = [v if ":" in v else "(class expression)" for v in row.pop(f"{key}_iris")]
    return rows


def resource_kinds(driver: Driver, iri: str) -> list[str] | None:
    """Labels of the resource with this IRI, or None if it is not in the database."""
    rows = read(driver, "MATCH (n:Resource {uri: $iri}) RETURN labels(n) AS labels", iri=iri)
    return rows[0]["labels"] if rows else None


def describe_class(driver: Driver, iri: str) -> dict[str, Any] | None:
    """Label, definition, annotations, super/sub classes, related properties of a class."""
    rows = read(
        driver,
        "MATCH (n:Resource {uri: $iri}) "
        "RETURN n.uri AS iri, labels(n) AS labels, properties(n) AS props",
        iri=iri,
    )
    if not rows:
        return None
    node = rows[0]
    props = {k: v for k, v in node["props"].items() if k != "uri"}
    item = "{iri: x.uri, label: coalesce(x.rdfs__label, x.skos__prefLabel)}"

    def related(*patterns: str) -> list[dict[str, Any]]:
        query = " UNION ".join(
            f"MATCH (n:Resource {{uri: $iri}}) MATCH {p} RETURN DISTINCT {item} AS item"
            for p in patterns
        )
        res = read(driver, query, iri=iri)
        return sorted(_named([r["item"] for r in res]), key=lambda x: x["label"] or x["iri"])

    restrictions = class_restrictions(driver, iri)
    supers = superclass_distances(driver, iri)
    return {
        "iri": node["iri"],
        "types": node["labels"],
        "is_class": "owl__Class" in node["labels"],
        "label": next((props[p] for p in LABEL_PROPS if p in props), None),
        "definition": next((props[p] for p in DEFINITION_PROPS if p in props), None),
        "annotations": props,
        "superclasses": related("(n)-[:rdfs__subClassOf]->(x)"),
        "subclasses": related("(x)-[:rdfs__subClassOf]->(n)"),
        "equivalent_classes": related("(n)-[:owl__equivalentClass]-(x)"),
        "properties_with_domain": properties_on(driver, "rdfs__domain", supers),
        "properties_with_range": properties_on(driver, "rdfs__range", supers),
        "restrictions": restrictions,
    }


# Relationships that make up OWL class expressions (intersections, unions and their lists).
_EXPRESSION_PATH = "owl__intersectionOf|owl__unionOf|rdf__first|rdf__rest"


def class_restrictions(driver: Driver, iri: str) -> list[dict[str, Any]]:
    """Property restrictions in the class's rdfs:subClassOf / owl:equivalentClass axioms.

    Restrictions nested in intersectionOf / unionOf lists are included (IOF defines many
    classes as ``A and (p some B)``). ``within_union`` marks ones inside a union, which are
    alternatives rather than necessary conditions.
    """
    rows = read(
        driver,
        "MATCH (n:Resource {uri: $iri})-[axiom:rdfs__subClassOf|owl__equivalentClass]->(x) "
        f"MATCH path = (x)-[:{_EXPRESSION_PATH}*0..20]->(r:owl__Restriction) "
        "MATCH (r)-[:owl__onProperty]->(p) "
        "OPTIONAL MATCH (r)-[q:owl__someValuesFrom|owl__allValuesFrom|owl__hasValue"
        "|owl__onClass]->(v) "
        "RETURN DISTINCT replace(replace(type(axiom), 'rdfs__', ''), 'owl__', '') AS via, "
        "any(rel IN relationships(path) WHERE type(rel) = 'owl__unionOf') AS within_union, "
        "p.uri AS property, coalesce(p.rdfs__label, p.skos__prefLabel) AS property_label, "
        "replace(type(q), 'owl__', '') AS restriction, v.uri AS value, "
        "coalesce(v.rdfs__label, v.skos__prefLabel) AS value_label, "
        "coalesce(r.owl__cardinality, r.owl__minCardinality, r.owl__maxCardinality, "
        "r.owl__qualifiedCardinality, r.owl__minQualifiedCardinality, "
        "r.owl__maxQualifiedCardinality) AS cardinality",
        iri=iri,
    )
    for r in rows:
        if r["value"] and ":" not in r["value"]:
            r["value"] = "(class expression)"
    rows.sort(key=lambda r: (r["within_union"], r["via"], r["property_label"] or r["property"]))
    return rows


def superclass_distances(driver: Driver, iri: str) -> dict[str, int]:
    """The class itself (0) and its named rdfs:subClassOf ancestors with their distance."""
    rows = read(
        driver,
        "MATCH path = (n:Resource {uri: $iri})-[:rdfs__subClassOf*0..20]->(s) "
        "WHERE s.uri CONTAINS ':' RETURN s.uri AS iri, min(length(path)) AS distance",
        iri=iri,
    )
    return {r["iri"]: r["distance"] for r in rows}


def properties_on(driver: Driver, rel: str, classes: dict[str, int]) -> list[dict[str, Any]]:
    """Properties whose ``rel`` (rdfs__domain / rdfs__range) is one of ``classes``, directly
    or as a member of an owl:unionOf. ``via`` is the class it is declared on (the class
    itself or an ancestor) and ``distance`` how many subClassOf steps away that is."""
    if not classes:
        return []
    rows = read(
        driver,
        f"MATCH (x:Resource)-[:{rel}]->(d) WHERE x.uri CONTAINS ':' "
        "OPTIONAL MATCH (d)-[:owl__unionOf]->()-[:rdf__rest*0..]->()-[:rdf__first]->(m) "
        "WITH x, d, collect(m.uri) AS members "
        "WITH x, [c IN $classes WHERE c = d.uri OR c IN members] AS hits WHERE size(hits) > 0 "
        "RETURN x.uri AS iri, coalesce(x.rdfs__label, x.skos__prefLabel) AS label, "
        "CASE WHEN x:owl__ObjectProperty THEN 'object' "
        "WHEN x:owl__DatatypeProperty THEN 'datatype' ELSE 'other' END AS kind, "
        "collect(hits) AS hits",
        classes=list(classes),
    )
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        via = min((c for hits in row.pop("hits") for c in hits), key=lambda c: classes[c])
        best = out.get(row["iri"])
        if best is None or classes[via] < best["distance"]:
            out[row["iri"]] = {**row, "via": via, "distance": classes[via]}
    return sorted(out.values(), key=lambda r: (r["distance"], r["label"] or r["iri"]))
