import pytest
from rdflib import RDF, RDFS, Graph, Literal, URIRef

from iof_gdb import prefixes as px

P = px.build_prefixes("https://example.org/inst/")
CONSTR = "https://spec.industrialontologies.org/ontology/construct/"


def test_namespace_matches_rdflib_neo4j():
    assert px.namespace_of("http://purl.obolibrary.org/obo/BFO_0000040") == P["bfo"]
    assert px.namespace_of(CONSTR + "MaterialArtifact") == P["iofconstr"]
    assert px.namespace_of(str(RDFS.label)) == P["rdfs"]


def test_find_unregistered_checks_predicates_and_types_only():
    g = Graph()
    s = URIRef("http://unregistered.example/subjects/a")  # subjects are never shortened
    g.add((s, RDF.type, URIRef(CONSTR + "PieceOfEquipment")))
    g.add((s, RDFS.label, Literal("a")))
    g.add((s, URIRef("http://other.example/vocab#prop"), URIRef("http://x.example/obj")))
    g.add((s, RDF.type, URIRef("http://types.example/T")))
    assert px.find_unregistered(g, P) == {"http://other.example/vocab#", "http://types.example/"}


def test_generate_prefixes_is_deterministic_and_unique():
    ns = ["https://a.example/ontology/maintenance/Maintenance/", "https://b.example/maintenance/"]
    new = px.generate_prefixes(ns, P)
    assert set(new.values()) == set(ns)
    assert len(set(new)) == 2
    assert all(px.is_valid_prefix_name(n) for n in new)
    assert new == px.generate_prefixes(list(reversed(ns)), P)
    assert px.generate_prefixes([P["rdfs"]], P) == {}


@pytest.mark.parametrize(
    "value, expected",
    [
        ("bfo:BFO_0000040", "http://purl.obolibrary.org/obo/BFO_0000040"),
        ("bfo__BFO_0000040", "http://purl.obolibrary.org/obo/BFO_0000040"),
        ("iofconstr:MaterialArtifact", CONSTR + "MaterialArtifact"),
        ("inst:P-101", "https://example.org/inst/P-101"),
        ("<http://x.example/a>", "http://x.example/a"),
        ("http://x.example/a", "http://x.example/a"),
        ("urn:x:y", "urn:x:y"),
    ],
)
def test_expand(value, expected):
    assert px.expand(value, P) == expected


def test_expand_unknown_prefix():
    with pytest.raises(ValueError, match="nope"):
        px.expand("nope:x", P)


def test_shorten():
    assert px.shorten(str(RDFS.subClassOf), P) == "rdfs__subClassOf"
    assert px.shorten("http://purl.obolibrary.org/obo/BFO_0000040", P) == "bfo__BFO_0000040"
    assert px.shorten("http://unknown.example/x", P) is None
