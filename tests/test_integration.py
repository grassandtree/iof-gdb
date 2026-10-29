"""Integration tests against a real Neo4j (skipped when it is not configured or reachable)."""

import pytest

from iof_gdb import db, ontology, rdf_import
from iof_gdb.profile import names_for

pytestmark = pytest.mark.integration


def test_init_creates_constraint(neo4j_driver):
    info = db.init(neo4j_driver)
    assert any(c["properties"] == ["uri"] for c in info["constraints"])


def test_import_small_ttl(neo4j_settings, neo4j_driver, test_ns):
    ttl = f"""
    @prefix owl:  <http://www.w3.org/2002/07/owl#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    <{test_ns}A> a owl:Class ; rdfs:label "A" ; rdfs:subClassOf <{test_ns}B> .
    <{test_ns}B> a owl:Class ; rdfs:label "B" .
    """
    summary = rdf_import.import_text(ttl, settings=neo4j_settings)
    assert summary.triples == 5
    rows = db.read(
        neo4j_driver,
        "MATCH (r:Resource) WHERE r.uri STARTS WITH $ns RETURN count(r) AS c",
        ns=test_ns,
    )
    assert rows[0]["c"] == 2
    rows = db.read(
        neo4j_driver,
        "MATCH (a:owl__Class {uri: $a})-[:rdfs__subClassOf]->(b:owl__Class) "
        "RETURN a.rdfs__label AS a, b.rdfs__label AS b",
        a=test_ns + "A",
    )
    assert rows == [{"a": "A", "b": "B"}]


def test_describe_class_follows_class_expressions(neo4j_settings, neo4j_driver, test_ns):
    """Same shape as IOF's PieceOfEquipment: C == (A or B) and (hasRole some R), C < Top."""
    ttl = f"""
    @prefix owl:  <http://www.w3.org/2002/07/owl#> .
    @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
    <{test_ns}C> a owl:Class ; rdfs:label "c" ;
        rdfs:subClassOf <{test_ns}Top> ;
        owl:equivalentClass [ a owl:Class ; owl:intersectionOf (
            [ a owl:Class ; owl:unionOf ( <{test_ns}A> <{test_ns}B> ) ]
            [ a owl:Restriction ; owl:onProperty <{test_ns}hasRole> ;
              owl:someValuesFrom <{test_ns}R> ] ) ] .
    <{test_ns}Top> a owl:Class ; rdfs:label "top" .
    <{test_ns}hasRole> a owl:ObjectProperty ; rdfs:label "has role" ;
        rdfs:domain [ a owl:Class ; owl:unionOf ( <{test_ns}Top> <{test_ns}Other> ) ] .
    <{test_ns}direct> a owl:ObjectProperty ; rdfs:label "direct" ; rdfs:range <{test_ns}C> .
    """
    rdf_import.import_text(ttl, settings=neo4j_settings)
    d = ontology.describe_class(neo4j_driver, test_ns + "C", names=names_for("iof"))

    [r] = d["restrictions"]
    assert r["property"] == test_ns + "hasRole"
    assert r["restriction"] == "someValuesFrom" and r["value"] == test_ns + "R"
    assert r["via"] == "equivalentClass" and r["within_union"] is False

    [dom] = d["properties_with_domain"]
    assert (dom["iri"], dom["via"], dom["distance"]) == (test_ns + "hasRole", test_ns + "Top", 1)
    [rng] = d["properties_with_range"]
    assert (rng["iri"], rng["distance"]) == (test_ns + "direct", 0)
