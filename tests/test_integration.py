"""Integration tests against a real Neo4j (skipped when it is not configured or reachable)."""

import pytest

from iof_gdb import db, rdf_import

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
