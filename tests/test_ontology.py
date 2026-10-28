from fakes import FakeDriver

from iof_gdb import ontology

CLS = "https://spec.industrialontologies.org/ontology/construct/PieceOfEquipment"


def test_search_classes_passes_term_and_limit():
    driver = FakeDriver(
        lambda q, p: [{"iri": CLS, "label": "piece of equipment", "definition": "d"}]
    )
    rows = ontology.search_classes(driver, "Equipment", 5)
    assert rows[0]["iri"] == CLS
    query, params = driver.queries[0]
    assert "owl__Class" in query and params == {"term": "Equipment", "limit": 5}


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
    [row] = ontology.search_properties(driver, "role", kind="object")
    assert row["domain"] == ["(class expression)", "http://purl.obolibrary.org/obo/BFO_0000040"]
    assert row["range"] == ["http://purl.obolibrary.org/obo/BFO_0000023"]
    assert "owl__DatatypeProperty" not in driver.queries[0][0]


def test_search_properties_rejects_bad_kind():
    import pytest

    with pytest.raises(ValueError):
        ontology.search_properties(FakeDriver(lambda q, p: []), "x", kind="annotation")


def test_describe_class_missing():
    assert ontology.describe_class(FakeDriver(lambda q, p: []), CLS) is None


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
                        "iofav__naturalLanguageDefinition": "a material artifact ...",
                    },
                }
            ]
        if "owl__Restriction" in query:
            return [
                {
                    "property": "p",
                    "property_label": "has role",
                    "restriction": "someValuesFrom",
                    "value": "Nblank",
                    "value_label": None,
                    "cardinality": None,
                }
            ]
        if "(n)-[:rdfs__subClassOf]->(x)" in query:
            return [
                {"item": {"iri": "https://x.example/B", "label": "b"}},
                {"item": {"iri": "Nblank", "label": None}},  # restriction blank node
                {"item": {"iri": "https://x.example/A", "label": "a"}},
            ]
        return []

    driver = FakeDriver(respond)
    d = ontology.describe_class(driver, CLS)
    assert d["is_class"] and d["label"] == "piece of equipment"
    assert d["definition"] == "a material artifact ..."
    assert "uri" not in d["annotations"]
    assert [x["label"] for x in d["superclasses"]] == ["a", "b"]
    assert d["restrictions"][0]["value"] == "(class expression)"
    domain_query = next(q for q, _ in driver.queries if "rdfs__domain" in q)
    assert domain_query.count("RETURN DISTINCT") == 2 and " UNION " in domain_query
