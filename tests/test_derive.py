from iof_gdb import derive
from iof_gdb.profile import names_for

from .fakes import FakeSessionDriver

N = names_for("iof")


def test_queries_use_profile_names_and_never_touch_rdf_relationships():
    q = derive.queries(N)
    assert "`rdfs__subClassOf`" in q["explicit"] and "`owl__equivalentClass`" in q["inferred"]
    assert "`owl__intersectionOf`" in q["inferred"] and "unionOf" not in q["inferred"]
    assert "SUBCLASS_OF" in q["clear"] and "rdfs__subClassOf" not in q["clear"]
    assert "ON CREATE SET r.inferred = true" in q["inferred"]  # explicit edges stay explicit


def test_derive_runs_in_one_transaction_in_order():
    def respond(query):
        if "explicit," in query:
            return {"explicit": 125, "inferred": 3}
        return {"n": 1163 if "BlankNode" in query and "SET n:" in query else 0}

    driver = FakeSessionDriver(respond)
    result = derive.derive(driver, N)
    assert result == {
        "blank_nodes_labelled": 1163,
        "subclass_of_explicit": 125,
        "subclass_of_inferred": 3,
    }
    order = [next(k for k, v in derive.queries(N).items() if v == q) for q in driver.queries]
    assert order == ["blank_nodes", "clear", "explicit", "inferred", "count"]
