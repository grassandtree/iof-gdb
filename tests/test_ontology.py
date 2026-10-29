from iof_gdb import ontology
from iof_gdb.profile import names_for

from .fakes import FakeDriver

N = names_for("iof")

CLS = "https://spec.industrialontologies.org/ontology/construct/PieceOfEquipment"


def test_search_classes_passes_term_and_limit():
    driver = FakeDriver(
        lambda q, p: [{"iri": CLS, "label": "piece of equipment", "definition": "d"}]
    )
    rows = ontology.search_classes(driver, "Equipment", 5, names=N)
    assert rows[0]["iri"] == CLS
    query, params = driver.queries[0]
    assert "owl__Class" in query
    assert params["term"] == "Equipment" and params["limit"] == 5
    assert "rdfs__label" in params["text_props"] and "skos__altLabel" in params["text_props"]


def test_search_properties_marks_class_expressions():
    driver = FakeDriver(
        lambda q, p: [
            {
                "iri": "x:hasRole",
                "label": "has role",
                "definition": None,
                "kind": "object",
                "domain_iris": ["N1234abcd", "http://purl.obolibrary.org/obo/BFO_0000040"],
                "range_iris": ["http://purl.obolibrary.org/obo/BFO_0000023"],
            }
        ]
    )
    [row] = ontology.search_properties(driver, "role", kind="object", names=N)
    assert row["domain"] == ["(class expression)", "http://purl.obolibrary.org/obo/BFO_0000040"]
    assert row["range"] == ["http://purl.obolibrary.org/obo/BFO_0000023"]
    assert "owl__DatatypeProperty" not in driver.queries[0][0]


def test_search_properties_rejects_bad_kind():
    import pytest

    with pytest.raises(ValueError):
        ontology.search_properties(FakeDriver(lambda q, p: []), "x", kind="annotation", names=N)


def test_describe_class_missing():
    assert ontology.describe_class(FakeDriver(lambda q, p: []), CLS, names=N) is None


def test_describe_class_shapes_result():
    def respond(query, params):
        if "properties(n) AS props" in query:
            return [
                {
                    "iri": CLS,
                    "labels": ["Resource", "owl__Class"],
                    "props": {
                        "uri": CLS,
                        "rdfs__label": "piece of equipment",
                        "rdfs__label@en": "piece of equipment",
                        "rdfs__label@ko": "장비",
                        "skos__example": ["e1", "e2"],
                        "iofav__naturalLanguageDefinition": "a material artifact ...",
                    },
                }
            ]
        if "owl__Restriction" in query:
            return [
                {
                    "via": "equivalentClass",
                    "within_union": True,
                    "property": "p2",
                    "property_label": "b prop",
                    "restriction": "someValuesFrom",
                    "value": "x:V",
                    "value_label": "v",
                    "cardinality": None,
                },
                {
                    "via": "equivalentClass",
                    "within_union": False,
                    "property": "p",
                    "property_label": "has role",
                    "restriction": "someValuesFrom",
                    "value": "Nblank",
                    "value_label": None,
                    "cardinality": None,
                },
            ]
        if "min(length(path)) AS distance" in query:
            return [{"iri": CLS, "distance": 0}, {"iri": "bfo:ME", "distance": 1}]
        if "`rdfs__domain`]->(d)" in query:
            assert params["classes"] == [CLS, "bfo:ME"]
            return [
                {"iri": "x:p1", "label": "p1", "kind": "object", "hits": [["bfo:ME"]]},
                {"iri": "x:p0", "label": "p0", "kind": "object", "hits": [["bfo:ME"], [CLS]]},
            ]
        if "(n)-[:`rdfs__subClassOf`]->(x)" in query:
            return [
                {"item": {"iri": "https://x.example/B", "label": "b"}},
                {"item": {"iri": "Nblank", "label": None}},  # restriction blank node
                {"item": {"iri": "https://x.example/A", "label": "a"}},
            ]
        return []

    driver = FakeDriver(respond)
    d = ontology.describe_class(driver, CLS, names=N)
    assert d["is_class"] and d["label"] == "piece of equipment"
    assert d["definition"] == "a material artifact ..."
    assert "uri" not in d["annotations"]
    assert d["labels"] == {"en": "piece of equipment", "ko": "장비"}
    assert d["annotations"]["skos__example"] == ["e1", "e2"]
    assert [x["label"] for x in d["superclasses"]] == ["a", "b"]
    # necessary conditions first, blank values hidden
    assert [r["property"] for r in d["restrictions"]] == ["p", "p2"]
    assert d["restrictions"][0]["value"] == "(class expression)"
    # inherited domains: closest declaration wins, sorted by distance
    assert [(p["iri"], p["via"], p["distance"]) for p in d["properties_with_domain"]] == [
        ("x:p0", CLS, 0),
        ("x:p1", "bfo:ME", 1),
    ]
    assert d["properties_with_range"] == []
    restriction_query = next(q for q, _ in driver.queries if "owl__Restriction" in q)
    assert "`owl__intersectionOf`|`owl__unionOf`|`rdf__first`|`rdf__rest`" in restriction_query


def test_label_expression_handles_arrays_and_languages():
    expr = N.label("n")
    assert expr.startswith("head([] + coalesce(")
    assert "n.`rdfs__label`" in expr and "n.`rdfs__label@ko`" in expr
    assert expr.index("`rdfs__label`") < expr.index("`rdfs__label@en`")


def test_korean_only_node_still_gets_a_label():
    props = {"rdfs__label@ko": "펌프"}
    assert ontology._first(ontology._pick(props, N.with_languages(N.label_props))) == "펌프"
