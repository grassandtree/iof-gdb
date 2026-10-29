"""Ontology exploration queries over the rdflib-neo4j (n10s-style) graph.

Only the storage convention and OWL/RDFS structure are assumed here: every resource is a
``:Resource {uri}`` node, vocabulary is stored as ``prefix__localName``, blank nodes (OWL
restrictions, unionOf lists, ...) are :Resource nodes whose ``uri`` is the rdflib blank-node
id (no ':' in it). Which rdf:types count as classes and which properties hold labels or
definitions come from the active profile via ``GraphNames``.
"""

from __future__ import annotations

from typing import Any

from neo4j import Driver

from .db import read
from .profile import GraphNames

_NAMED = "n.uri CONTAINS ':'"
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


# Any label / synonym property, plain or @lang, single value or array, containing $term.
_MATCHES_TERM = (
    "(any(k IN keys(n) WHERE any(b IN $text_props WHERE k = b OR k STARTS WITH b + '@') "
    "AND any(v IN [] + n[k] WHERE toLower(toString(v)) CONTAINS toLower($term))) "
    "OR toLower(n.uri) CONTAINS toLower($term))"
)
# Language-specific labels as [property, value] pairs, turned into {lang: label} in Python.
_LANG_LABELS = (
    "[k IN keys(n) WHERE any(b IN $label_props WHERE k STARTS WITH b + '@') | [k, n[k]]] "
    "AS lang_labels"
)


def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else value


def by_language(props: dict[str, Any], bases: tuple[str, ...] | list[str]) -> dict[str, Any]:
    """{lang: value} from ``base@lang`` properties; earlier bases win."""
    out: dict[str, Any] = {}
    for base in bases:
        for key, value in props.items():
            if key.startswith(base + "@"):
                out.setdefault(key[len(base) + 1 :], value)
    return out


def _lang_labels(row: dict[str, Any], names: GraphNames) -> None:
    pairs = row.pop("lang_labels", None) or []
    row["labels"] = {
        lang: _first(v) for lang, v in by_language(dict(pairs), names.label_props).items()
    }


def search_classes(
    driver: Driver, term: str, limit: int = 20, *, names: GraphNames
) -> list[dict[str, Any]]:
    """Classes (profile class_types) whose label / synonym / IRI contains ``term``."""
    rows = read(
        driver,
        f"MATCH (n:Resource) WHERE {names.is_class('n')} AND {_NAMED} AND {_MATCHES_TERM} "
        f"WITH n, n.uri AS iri, {names.label('n')} AS label "
        f"RETURN iri, label, {_LANG_LABELS}, {names.definition('n')} AS definition "
        f"{_ORDER} LIMIT $limit",
        term=term,
        limit=limit,
        text_props=names.text_props,
        label_props=list(names.label_props),
    )
    for row in rows:
        _lang_labels(row, names)
    return rows


def search_properties(
    driver: Driver, term: str, limit: int = 20, kind: str | None = None, *, names: GraphNames
) -> list[dict[str, Any]]:
    """Object / datatype properties whose label / IRI contains ``term``."""
    by_kind = {"object": names.object_property, "datatype": names.datatype_property}
    if kind is not None and kind not in by_kind:
        raise ValueError("kind must be 'object' or 'datatype'")
    labels = [by_kind[kind]] if kind else list(by_kind.values())
    q = names.q
    rows = read(
        driver,
        f"MATCH (n:Resource) WHERE {names.has_label('n', labels)} AND {_NAMED} "
        f"AND {_MATCHES_TERM} "
        f"WITH n, n.uri AS iri, {names.label('n')} AS label "
        f"OPTIONAL MATCH (n)-[:{q(names.domain)}]->(d) "
        f"OPTIONAL MATCH (n)-[:{q(names.range)}]->(r) "
        "WITH n, iri, label, collect(DISTINCT d.uri) AS domain_iris, "
        "collect(DISTINCT r.uri) AS range_iris "
        f"RETURN iri, label, {names.definition('n')} AS definition, "
        f"CASE WHEN n:{q(names.object_property)} THEN 'object' ELSE 'datatype' END AS kind, "
        f"domain_iris, range_iris {_ORDER} LIMIT $limit",
        term=term,
        limit=limit,
        text_props=names.text_props,
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


def describe_class(driver: Driver, iri: str, *, names: GraphNames) -> dict[str, Any] | None:
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
    item = f"{{iri: x.uri, label: {names.label('x')}}}"
    sub, equiv = names.q(names.sub_class_of), names.q(names.equivalent_class)

    def related(*patterns: str) -> list[dict[str, Any]]:
        query = " UNION ".join(
            f"MATCH (n:Resource {{uri: $iri}}) MATCH {p} RETURN DISTINCT {item} AS item"
            for p in patterns
        )
        res = read(driver, query, iri=iri)
        return sorted(_named([r["item"] for r in res]), key=lambda x: x["label"] or x["iri"])

    supers = superclass_distances(driver, iri, names=names)
    return {
        "iri": node["iri"],
        "types": node["labels"],
        "is_class": any(label in node["labels"] for label in names.class_labels),
        "label": _first(_pick(props, names.with_languages(names.label_props))),
        "labels": {k: _first(v) for k, v in by_language(props, names.label_props).items()},
        "definition": _first(_pick(props, names.with_languages(names.definition_props))),
        "definitions": by_language(props, names.definition_props),
        "annotations": props,
        "superclasses": related(f"(n)-[:{sub}]->(x)"),
        "subclasses": related(f"(x)-[:{sub}]->(n)"),
        "equivalent_classes": related(f"(n)-[:{equiv}]-(x)"),
        "properties_with_domain": properties_on(driver, names.domain, supers, names=names),
        "properties_with_range": properties_on(driver, names.range, supers, names=names),
        "restrictions": class_restrictions(driver, iri, names=names),
    }


def _pick(props: dict[str, Any], keys: tuple[str, ...]) -> Any:
    return next((props[k] for k in keys if k in props), None)


def class_restrictions(driver: Driver, iri: str, *, names: GraphNames) -> list[dict[str, Any]]:
    """Property restrictions in the class's rdfs:subClassOf / owl:equivalentClass axioms.

    Restrictions nested in intersectionOf / unionOf lists are included (IOF, for one, defines
    many classes as ``A and (p some B)``). ``within_union`` marks ones inside a union, which
    are alternatives rather than necessary conditions.
    """
    q = names.q
    axioms = names.rel_types(names.sub_class_of, names.equivalent_class)
    expression_path = names.rel_types(
        names.intersection_of, names.union_of, names.first, names.rest
    )
    values = names.rel_types(*names.restriction_values)
    rows = read(
        driver,
        f"MATCH (n:Resource {{uri: $iri}})-[axiom:{axioms}]->(x) "
        f"MATCH path = (x)-[:{expression_path}*0..20]->(r:{q(names.restriction)}) "
        f"MATCH (r)-[:{q(names.on_property)}]->(p) "
        f"OPTIONAL MATCH (r)-[v_rel:{values}]->(v) "
        "RETURN DISTINCT type(axiom) AS via, "
        f"any(rel IN relationships(path) WHERE type(rel) = $union_of) AS within_union, "
        f"p.uri AS property, {names.label('p')} AS property_label, "
        "type(v_rel) AS restriction, v.uri AS value, "
        f"{names.label('v')} AS value_label, "
        f"{names.coalesce('r', names.cardinalities)} AS cardinality",
        iri=iri,
        union_of=names.union_of,
    )
    for r in rows:
        r["via"] = _local(r["via"])
        r["restriction"] = _local(r["restriction"]) if r["restriction"] else None
        if r["value"] and ":" not in r["value"]:
            r["value"] = "(class expression)"
    rows.sort(key=lambda r: (r["within_union"], r["via"], r["property_label"] or r["property"]))
    return rows


def _local(stored_name: str) -> str:
    """``owl__someValuesFrom`` -> ``someValuesFrom``."""
    return stored_name.split("__", 1)[-1]


def superclass_distances(driver: Driver, iri: str, *, names: GraphNames) -> dict[str, int]:
    """The class itself (0) and its named rdfs:subClassOf ancestors with their distance."""
    rows = read(
        driver,
        f"MATCH path = (n:Resource {{uri: $iri}})-[:{names.q(names.sub_class_of)}*0..20]->(s) "
        "WHERE s.uri CONTAINS ':' RETURN s.uri AS iri, min(length(path)) AS distance",
        iri=iri,
    )
    return {r["iri"]: r["distance"] for r in rows}


def properties_on(
    driver: Driver, rel: str, classes: dict[str, int], *, names: GraphNames
) -> list[dict[str, Any]]:
    """Properties whose ``rel`` (domain / range) is one of ``classes``, directly or as a
    member of an owl:unionOf. ``via`` is the class it is declared on (the class itself or an
    ancestor) and ``distance`` how many subClassOf steps away that is."""
    if not classes:
        return []
    q = names.q
    rows = read(
        driver,
        f"MATCH (x:Resource)-[:{q(rel)}]->(d) WHERE x.uri CONTAINS ':' "
        f"OPTIONAL MATCH (d)-[:{q(names.union_of)}]->()-[:{q(names.rest)}*0..]->()"
        f"-[:{q(names.first)}]->(m) "
        "WITH x, d, collect(m.uri) AS members "
        "WITH x, [c IN $classes WHERE c = d.uri OR c IN members] AS hits WHERE size(hits) > 0 "
        f"RETURN x.uri AS iri, {names.label('x')} AS label, "
        f"CASE WHEN x:{q(names.object_property)} THEN 'object' "
        f"WHEN x:{q(names.datatype_property)} THEN 'datatype' ELSE 'other' END AS kind, "
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
