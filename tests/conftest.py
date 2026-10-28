import uuid

import pytest

from iof_gdb.config import ConfigError, create_driver, load_settings


@pytest.fixture(scope="session")
def neo4j_settings():
    """Settings for a reachable Neo4j, or skip (no settings / network blocked)."""
    try:
        settings = load_settings()
    except ConfigError as e:
        pytest.skip(f"Neo4j not configured: {e}")
    driver = create_driver(settings, connection_timeout=5, connection_acquisition_timeout=10)
    try:
        driver.verify_connectivity()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Neo4j not reachable ({type(e).__name__}): {e}")
    finally:
        driver.close()
    return settings


@pytest.fixture
def neo4j_driver(neo4j_settings):
    driver = create_driver(neo4j_settings)
    yield driver
    driver.close()


@pytest.fixture
def test_ns(neo4j_driver):
    """A unique IRI namespace for test resources; everything under it is deleted afterwards."""
    ns = f"https://test.example/iof-gdb-it/{uuid.uuid4().hex}/"
    yield ns
    neo4j_driver.execute_query(
        "MATCH (r:Resource) WHERE r.uri STARTS WITH $ns DETACH DELETE r", {"ns": ns}
    )
