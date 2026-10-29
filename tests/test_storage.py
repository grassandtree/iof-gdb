import pytest
from rdflib import OWL, RDF, RDFS, BNode, Graph, Literal, URIRef
from rdflib_neo4j import HANDLE_MULTIVAL_STRATEGY, HANDLE_VOCAB_URI_STRATEGY
from rdflib_neo4j.Neo4jTriple import Neo4jTriple

from iof_gdb import storage
from iof_gdb.config import Settings, create_store_config
from iof_gdb.prefixes import build_prefixes

EX = "https://test.example/"
S = URIRef(EX + "Pump")
SKOS_ALT = URIRef("http://www.w3.org/2004/02/skos/core#altLabel")
RESTRICTION_TTL = """
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
<https://test.example/C> rdfs:subClassOf [ a owl:Restriction ;
    owl:onProperty <https://test.example/p> ; owl:someValuesFrom <https://test.example/D> ] .
"""


def lit_values(g, p):
    return sorted(str(o) for o in g.objects(S, URIRef(p)))


def test_split_languages_keeps_plain_value_and_per_language_copies():
    g = Graph()
    g.add((S, RDFS.label, Literal("pump", lang="en-US")))
    g.add((S, RDFS.label, Literal("펌프", lang="ko")))
    g.add((S, RDFS.comment, Literal("untagged")))
    g.add((S, RDFS.comment, Literal("commentaire", lang="fr")))
    out = storage.split_languages(g, "en")
    label = str(RDFS.label)
    assert lit_values(out, label) == ["pump"]  # primary language copy
    assert lit_values(out, label + "@en") == ["pump"]  # en-US -> en
    assert lit_values(out, label + "@ko") == ["펌프"]
    assert lit_values(out, str(RDFS.comment)) == ["untagged"]  # untagged wins over primary
    assert lit_values(out, str(RDFS.comment) + "@fr") == ["commentaire"]
    assert all(not o.language for o in out.objects() if isinstance(o, Literal))


def test_korean_only_import_does_not_touch_plain_property():
    g = Graph()
    g.add((S, RDFS.label, Literal("펌프", lang="ko")))
    out = storage.split_languages(g, "en")
    assert lit_values(out, str(RDFS.label)) == []
    assert lit_values(out, str(RDFS.label) + "@ko") == ["펌프"]


def test_base_predicate():
    assert storage.base_predicate(str(RDFS.label) + "@ko") == str(RDFS.label)
    assert storage.base_predicate("mailto:a@example.org") == "mailto:a@example.org"


def test_canonical_bnodes_are_deterministic_and_colon_free():
    a = storage.canonical_bnodes(Graph().parse(data=RESTRICTION_TTL, format="turtle"))
    b = storage.canonical_bnodes(Graph().parse(data=RESTRICTION_TTL, format="turtle"))
    assert set(a) == set(b)
    bnodes = {x for t in a for x in (t[0], t[2]) if isinstance(x, BNode)}
    assert len(bnodes) == 1 and all(":" not in str(x) for x in bnodes)
    assert (None, RDF.type, OWL.Restriction) in a


def test_multivalue_conflicts_and_prepare():
    g = Graph()
    g.add((S, SKOS_ALT, Literal("a", lang="en")))
    g.add((S, SKOS_ALT, Literal("b", lang="en")))
    g.add((S, RDFS.label, Literal("x")))
    g.add((S, RDFS.label, Literal("y")))
    with pytest.raises(storage.MultiValueError) as exc:
        storage.prepare(g, ["en"], [str(SKOS_ALT)])
    assert list(exc.value.conflicts) == [str(RDFS.label)]
    g.remove((S, RDFS.label, Literal("y")))
    prepared = storage.prepare(g, ["en"], [str(SKOS_ALT)])
    assert prepared.array_predicates == {str(SKOS_ALT), str(SKOS_ALT) + "@en"}
    # different languages of a single-valued predicate are not a conflict
    g2 = Graph()
    g2.add((S, RDFS.label, Literal("pump", lang="en")))
    g2.add((S, RDFS.label, Literal("펌프", lang="ko")))
    assert storage.prepare(g2, ["en", "ko"], []).array_predicates == set()


def test_store_config_arrays_only_when_listed():
    settings = Settings(uri="neo4j://x", username="u", password="p")
    prefixes = build_prefixes()
    plain = create_store_config(settings, prefixes)
    assert plain.handle_multival_strategy is HANDLE_MULTIVAL_STRATEGY.OVERWRITE
    arrays = create_store_config(settings, prefixes, [str(SKOS_ALT), str(SKOS_ALT) + "@ko"])
    assert arrays.handle_multival_strategy is HANDLE_MULTIVAL_STRATEGY.ARRAY
    assert set(arrays.multival_props_names) == {str(SKOS_ALT), str(SKOS_ALT) + "@ko"}


def test_rdflib_neo4j_stores_listed_language_variant_as_array():
    """Our names go through rdflib-neo4j's own triple handling as expected."""
    settings = Settings(uri="neo4j://x", username="u", password="p")
    prefixes = build_prefixes()
    config = create_store_config(settings, prefixes, [str(SKOS_ALT) + "@ko"])
    triple = Neo4jTriple(
        uri=S,
        handle_vocab_uri_strategy=HANDLE_VOCAB_URI_STRATEGY.SHORTEN,
        handle_multival_strategy=config.handle_multival_strategy,
        multival_props_names=config.multival_props_names,
        prefixes={ns: name for name, ns in config.get_prefixes().items()},
    )
    triple.parse_triple((S, URIRef(str(SKOS_ALT) + "@ko"), Literal("펌프")), {})
    triple.parse_triple((S, RDFS.label, Literal("pump")), {})
    assert triple.multi_props == {"skos__altLabel@ko": ["펌프"]}
    assert triple.props == {"rdfs__label": "pump"}
