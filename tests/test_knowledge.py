import pytest
from rdflib import OWL, RDF, RDFS, Literal, URIRef

from iof_gdb import knowledge as kn
from iof_gdb.prefixes import build_prefixes
from iof_gdb.profile import load_profile, names_for

IOF = load_profile("iof")

INST = "https://example.org/inst/"
C = "https://spec.industrialontologies.org/ontology/construct/"
KINDS = {
    C + "PieceOfEquipment": ["Resource", "owl__Class"],
    C + "hasRole": ["Resource", "owl__ObjectProperty"],
    C + "hasValue": ["Resource", "owl__DatatypeProperty"],
    INST + "R1": ["Resource", "owl__NamedIndividual"],
    INST + "P-101": None,
}


def vocab():
    return kn.Vocabulary(build_prefixes(INST, domain=IOF.prefixes), names_for("iof"), dict(KINDS))


def test_build_individual():
    g = kn.build_individual(
        "inst:P-101",
        "iofconstr:PieceOfEquipment",
        "Pump P-101",
        {
            "iofconstr:hasRole": "inst:R1",
            "iofconstr:hasValue": [3, {"value": "x", "lang": "en"}],
            "rdfs:comment": "a pump",
        },
        vocab(),
    )
    s = URIRef(INST + "P-101")
    assert (s, RDF.type, OWL.NamedIndividual) in g
    assert (s, RDF.type, URIRef(C + "PieceOfEquipment")) in g
    assert (s, RDFS.label, Literal("Pump P-101")) in g
    assert (s, URIRef(C + "hasRole"), URIRef(INST + "R1")) in g
    assert (s, URIRef(C + "hasValue"), Literal(3)) in g
    assert (s, URIRef(C + "hasValue"), Literal("x", lang="en")) in g
    assert (s, RDFS.comment, Literal("a pump")) in g


@pytest.mark.parametrize(
    "cls, props, message",
    [
        ("iofconstr:Nope", None, "not in the database"),
        ("inst:R1", None, "not a class"),
        ("iofconstr:PieceOfEquipment", {"iofconstr:unknown": "x"}, "search_properties"),
        ("iofconstr:PieceOfEquipment", {"iofconstr:hasRole": "inst:missing"}, "add it first"),
        ("iofconstr:PieceOfEquipment", {"iofconstr:hasRole": 5}, "object property"),
        ("nope:X", None, "Unknown prefix"),
    ],
)
def test_build_individual_rejects(cls, props, message):
    with pytest.raises(kn.KnowledgeError, match=message):
        kn.build_individual("inst:P-101", cls, None, props, vocab())


def test_build_relation():
    v = vocab()
    v.kinds[INST + "P-101"] = ["Resource"]
    g = kn.build_relation("inst:P-101", "iofconstr:hasRole", "inst:R1", v)
    assert list(g) == [(URIRef(INST + "P-101"), URIRef(C + "hasRole"), URIRef(INST + "R1"))]
    with pytest.raises(kn.KnowledgeError, match="datatype"):
        kn.build_relation("inst:P-101", "iofconstr:hasValue", "inst:R1", v)
    with pytest.raises(kn.KnowledgeError, match="Object"):
        kn.build_relation("inst:P-101", "iofconstr:hasRole", "inst:nope", v)


def test_lookup_vocabulary_skips_text_values():
    from fakes import FakeDriver

    from iof_gdb.config import Settings

    driver = FakeDriver(lambda q, p: [])
    settings = Settings(uri="x", username="u", password="p", instance_ns=INST)
    v = kn.lookup_vocabulary(
        driver, settings, ["inst:P-101"], ["plain text", "foo:bar", "inst:R1", "12:30"]
    )
    assert set(v.kinds) == {INST + "P-101", INST + "R1"}
    with pytest.raises(kn.KnowledgeError):
        kn.lookup_vocabulary(driver, settings, ["foo:bar"])
