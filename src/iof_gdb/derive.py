"""Derived structure rebuilt after every import (not part of the RDF data).

- ``:BlankNode`` label on blank nodes (OWL restrictions, unionOf/intersectionOf lists, ...),
  so queries can skip them with ``WHERE NOT n:BlankNode``.
- ``(:Resource)-[:SUBCLASS_OF {inferred}]->(:Resource)`` between *named* classes only:
  - ``inferred: false``: copied from ``rdfs:subClassOf`` (which also points to restriction
    blank nodes, i.e. conditions rather than superclasses);
  - ``inferred: true``: a named member of an ``owl:intersectionOf`` used in the class's
    ``owl:equivalentClass`` or ``rdfs:subClassOf`` (``A ≡ B ⊓ …`` implies ``A ⊑ B``). Members
    of unions are not superclasses and are skipped. This is the only inference rule; it is
    not a reasoner.

UPPER_CASE names mark these as derived, unlike the ``prefix__local`` names that come from RDF.
The original ``rdfs__subClassOf`` relationships are never touched.
"""

from __future__ import annotations

from typing import Any

from neo4j import Driver, ManagedTransaction

from .profile import GraphNames

BLANK_LABEL = "BlankNode"
SUBCLASS_REL = "SUBCLASS_OF"
_NAMED = "uri CONTAINS ':'"  # blank node ids never contain ':' (see storage.canonical_bnodes)


def queries(names: GraphNames) -> dict[str, str]:
    q = names.q
    sub, equiv = q(names.sub_class_of), q(names.equivalent_class)
    return {
        "blank_nodes": (
            f"MATCH (n:Resource) WHERE NOT n.{_NAMED} AND NOT n:{BLANK_LABEL} "
            f"SET n:{BLANK_LABEL} RETURN count(n) AS n"
        ),
        "clear": f"MATCH ()-[r:{SUBCLASS_REL}]->() DELETE r RETURN count(r) AS n",
        "explicit": (
            f"MATCH (a:Resource)-[:{sub}]->(b:Resource) "
            f"WHERE a.{_NAMED} AND b.{_NAMED} AND a <> b "
            f"MERGE (a)-[r:{SUBCLASS_REL}]->(b) SET r.inferred = false "
            "RETURN count(DISTINCT r) AS n"
        ),
        "inferred": (
            f"MATCH (a:Resource)-[:{sub}|{equiv}]->(x:Resource)"
            f"-[:{q(names.intersection_of)}]->(head) "
            f"WHERE a.{_NAMED} AND NOT x.{_NAMED} "
            f"MATCH (head)-[:{q(names.rest)}*0..]->()-[:{q(names.first)}]->(m:Resource) "
            f"WHERE m.{_NAMED} AND m <> a "
            f"MERGE (a)-[r:{SUBCLASS_REL}]->(m) ON CREATE SET r.inferred = true "
            "RETURN count(DISTINCT r) AS n"
        ),
        "count": (
            f"MATCH ()-[r:{SUBCLASS_REL}]->() "
            "RETURN sum(CASE WHEN r.inferred THEN 0 ELSE 1 END) AS explicit, "
            "sum(CASE WHEN r.inferred THEN 1 ELSE 0 END) AS inferred"
        ),
    }


def _rebuild(tx: ManagedTransaction, names: GraphNames) -> dict[str, Any]:
    q = queries(names)

    def one(key: str) -> Any:
        return tx.run(q[key]).single()

    labelled = one("blank_nodes")["n"]
    one("clear")
    one("explicit")
    one("inferred")
    counts = one("count")
    return {
        "blank_nodes_labelled": labelled,
        "subclass_of_explicit": counts["explicit"] or 0,
        "subclass_of_inferred": counts["inferred"] or 0,
    }


def derive(driver: Driver, names: GraphNames) -> dict[str, Any]:
    """Label blank nodes and rebuild SUBCLASS_OF in one transaction (readers never see a
    half-built hierarchy). Safe to run any number of times."""
    with driver.session() as session:
        return session.execute_write(_rebuild, names)
