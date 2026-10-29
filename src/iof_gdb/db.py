"""Database helpers: constraints, connectivity, stats, stored prefix definitions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from neo4j import Driver, RoutingControl

# Required by rdflib-neo4j (it MERGEs every node on :Resource {uri}).
UNIQUE_URI_CONSTRAINT = (
    "CREATE CONSTRAINT n10s_unique_uri IF NOT EXISTS FOR (r:Resource) REQUIRE r.uri IS UNIQUE"
)

# Same node n10s uses to keep prefix -> namespace definitions inside the database.
PREFIX_DEF_LABEL = "_NsPrefDef"


def read(driver: Driver, query: str, **params: Any) -> list[dict[str, Any]]:
    records, _, _ = driver.execute_query(query, params, routing_=RoutingControl.READ)
    return [r.data() for r in records]


def write(driver: Driver, query: str, **params: Any) -> list[dict[str, Any]]:
    records, _, _ = driver.execute_query(query, params, routing_=RoutingControl.WRITE)
    return [r.data() for r in records]


def init(driver: Driver) -> dict[str, Any]:
    """Check connectivity and create the uniqueness constraint rdflib-neo4j needs."""
    driver.verify_connectivity()
    info = driver.get_server_info()
    write(driver, UNIQUE_URI_CONSTRAINT)
    constraints = read(
        driver,
        "SHOW CONSTRAINTS YIELD name, labelsOrTypes, properties "
        "WHERE 'Resource' IN labelsOrTypes RETURN name, labelsOrTypes, properties",
    )
    return {
        "address": str(info.address),
        "agent": info.agent,
        "protocol_version": ".".join(map(str, info.protocol_version)),
        "constraints": constraints,
    }


def stats(driver: Driver) -> dict[str, Any]:
    """Node / relationship totals and counts per label and relationship type."""
    nodes = read(driver, "MATCH (n) RETURN count(n) AS c")[0]["c"]
    rels = read(driver, "MATCH ()-[r]->() RETURN count(r) AS c")[0]["c"]
    labels = read(
        driver,
        "MATCH (n) UNWIND labels(n) AS label "
        "RETURN label, count(*) AS count ORDER BY count DESC, label",
    )
    rel_types = read(
        driver,
        "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS count ORDER BY count DESC, type",
    )
    return {
        "nodes": nodes,
        "relationships": rels,
        "labels": {r["label"]: r["count"] for r in labels},
        "relationship_types": {r["type"]: r["count"] for r in rel_types},
    }


def load_prefix_defs(driver: Driver) -> dict[str, str]:
    rows = read(driver, f"MATCH (p:{PREFIX_DEF_LABEL}) RETURN properties(p) AS props")
    merged: dict[str, str] = {}
    for row in rows:
        merged.update({k: v for k, v in row["props"].items() if isinstance(v, str)})
    return merged


def save_prefix_defs(driver: Driver, prefixes: Mapping[str, str]) -> None:
    write(driver, f"MERGE (p:{PREFIX_DEF_LABEL}) SET p += $prefixes", prefixes=dict(prefixes))


def wrap_scalars_in_lists(driver: Driver, uris: Sequence[str], props: Sequence[str]) -> None:
    """Turn single values of ``props`` on these nodes into one-element lists.

    rdflib-neo4j appends to arrays with ``list + value``; on a plain string that would
    concatenate text instead. This matters when a property becomes multivalued after data
    was already loaded. ``[] + x`` is ``[x]`` for a scalar and ``x`` for a list.
    """
    for prop in props:
        q = "`" + prop.replace("`", "``") + "`"
        write(
            driver,
            f"UNWIND $uris AS u MATCH (n:Resource {{uri: u}}) "
            f"WHERE n.{q} IS NOT NULL AND NOT ([] + n.{q}) = n.{q} "
            f"SET n.{q} = [] + n.{q}",
            uris=list(uris),
        )


def reset(driver: Driver, batch_size: int = 5000) -> int:
    """Delete every node and relationship (constraints stay). Returns nodes deleted."""
    total = 0
    while True:
        rows = write(
            driver,
            "MATCH (n) WITH n LIMIT $batch DETACH DELETE n RETURN count(*) AS deleted",
            batch=batch_size,
        )
        deleted = rows[0]["deleted"] if rows else 0
        total += deleted
        if deleted == 0:
            return total
