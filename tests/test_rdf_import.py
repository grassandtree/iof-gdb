import sys
from pathlib import Path

import pytest
from rdflib import RDF, RDFS, Graph, Literal, URIRef
from rdflib.store import Store

from iof_gdb import rdf_import as ri
from iof_gdb.config import Settings
from iof_gdb.prefixes import build_prefixes

FIXTURES = Path(__file__).parent / "fixtures"
REMOTE_TTL = b"""@prefix owl: <http://www.w3.org/2002/07/owl#> .
<https://test.example/onto/remote> a owl:Ontology .
<https://test.example/onto/C> a owl:Class .
"""


def fake_fetch(responses):
    calls = []

    def fetch(url):
        calls.append(url)
        if url not in responses:
            raise OSError(f"no network for {url}")
        data, ctype = responses[url]
        return data, url, ctype

    fetch.calls = calls
    return fetch


@pytest.mark.parametrize(
    "location, fmt",
    [
        ("a.ttl", "turtle"),
        ("dir/b.OWL", "xml"),
        ("c.rdf", "xml"),
        ("d.nt", "nt"),
        ("e.jsonld", "json-ld"),
        ("https://x.example/onto/f.ttl?raw=1", "turtle"),
        ("https://x.example/onto/Core/", None),
        ("noext", None),
    ],
)
def test_guess_format(location, fmt):
    assert ri.guess_format(location) == fmt


@pytest.mark.parametrize(
    "data, fmt",
    [
        (b'<?xml version="1.0"?><rdf:RDF/>', "xml"),
        (b"\xef\xbb\xbf  <rdf:RDF xmlns:rdf='x'/>", "xml"),
        (b'{"@context": {}}', "json-ld"),
        (b"@prefix ex: <http://x/> .", "turtle"),
    ],
)
def test_sniff_format(data, fmt):
    assert ri.sniff_format(data) == fmt


def test_normalize_format():
    assert ri.normalize_format("TTL") == "turtle"
    assert ri.normalize_format("rdf/xml") == "xml"
    assert ri.normalize_format(None) is None


def test_url_format_from_content_type_then_sniff():
    fetch = fake_fetch(
        {
            "https://x.example/a": (b"<?xml version='1.0'?><rdf:RDF/>", "text/plain"),
            "https://x.example/b": (b"@prefix ex: <http://x/> .", "text/turtle"),
        }
    )
    assert ri.read_document("https://x.example/a", fetch=fetch).format == "xml"
    assert ri.read_document("https://x.example/b", fetch=fetch).format == "turtle"


def test_load_without_imports():
    loaded = ri.load_graph(str(FIXTURES / "root.ttl"), catalog=ri.Catalog(FIXTURES))
    assert loaded.ontologies == ["https://test.example/onto/root"]
    assert (URIRef("https://test.example/onto/A"), RDFS.label, Literal("A")) in loaded.graph
    assert len(loaded.sources) == 1


def test_follow_imports_prefers_catalog_and_skips_loaded():
    fetch = fake_fetch({"https://test.example/onto/remote": (REMOTE_TTL, "text/turtle")})
    loaded = ri.load_graph(
        str(FIXTURES / "root.ttl"),
        follow_imports=True,
        catalog=ri.Catalog(FIXTURES, scan=False),
        fetch=fetch,
    )
    # mid comes from the local catalog, remote from the (fake) network
    assert fetch.calls == ["https://test.example/onto/remote"]
    assert len(loaded.sources) == 3
    assert set(loaded.ontologies) == {
        "https://test.example/onto/root",
        "https://test.example/onto/mid-declared",
        "https://test.example/onto/2026/mid",
        "https://test.example/onto/remote",
    }
    # mid imports root (cycle) and its own versionIRI: neither is loaded again
    assert loaded.unresolved == {}
    assert (
        URIRef("https://test.example/onto/C"),
        RDF.type,
        URIRef("http://www.w3.org/2002/07/owl#Class"),
    ) in loaded.graph


def test_unresolvable_import_is_reported(tmp_path):
    loaded = ri.load_graph(
        str(FIXTURES / "root.ttl"),
        follow_imports=True,
        catalog=ri.Catalog(tmp_path, scan=False),
        fetch=fake_fetch({}),
    )
    assert set(loaded.unresolved) == {
        "https://test.example/onto/mid",
        "https://test.example/onto/remote",
    }


def test_catalog_scan_finds_declared_iris(tmp_path):
    (tmp_path / "mid.rdf").write_bytes((FIXTURES / "mid.rdf").read_bytes())
    cat = ri.Catalog(tmp_path)
    assert cat.resolve("https://test.example/onto/2026/mid") == (tmp_path / "mid.rdf").resolve()
    assert cat.resolve("https://test.example/onto/unknown") is None


def test_fetch_ontologies_writes_files_and_catalog(tmp_path):
    root = b"""@prefix owl: <http://www.w3.org/2002/07/owl#> .
<https://test.example/onto/root/> a owl:Ontology ;
    owl:versionIRI <https://test.example/onto/2026/root/> ;
    owl:imports <https://test.example/onto/remote> ."""
    fetch = fake_fetch(
        {
            "https://test.example/onto/root/": (root, "text/turtle"),
            "https://test.example/onto/remote": (REMOTE_TTL, "text/turtle"),
        }
    )
    saved = ri.fetch_ontologies(["https://test.example/onto/root/"], tmp_path, fetch=fetch)
    assert {p.name for p in saved.values()} == {"root.ttl", "remote.ttl"}
    cat = ri.Catalog(tmp_path, scan=False)
    assert cat.resolve("https://test.example/onto/2026/root/") == (tmp_path / "root.ttl").resolve()
    loaded = ri.load_graph(
        "https://test.example/onto/root/", follow_imports=True, catalog=cat, fetch=fetch
    )
    assert len(loaded.sources) == 2
    assert fetch.calls.count("https://test.example/onto/remote") == 1  # only during fetch


def test_resolve_prefixes():
    g = Graph()
    s = URIRef("https://example.org/x")
    g.add((s, RDF.type, URIRef("https://new.example/vocab/Thing")))
    known = build_prefixes()
    with pytest.raises(ri.UnregisteredNamespaceError) as exc:
        ri.resolve_prefixes(g, known, auto_prefix=False)
    assert exc.value.namespaces == ["https://new.example/vocab/"]
    prefixes, new = ri.resolve_prefixes(g, known, auto_prefix=True)
    assert new == {"vocab": "https://new.example/vocab/"}
    assert prefixes["vocab"] == "https://new.example/vocab/"


class RecordingStore(Store):
    """Stand-in for Neo4jStore that records what rdflib sends it."""

    instances: list = []

    def __init__(self, config=None):
        super().__init__()
        self.config = config
        self.added = []
        self.closed_with = None
        self.driver = None
        print("store says hello")  # rdflib-neo4j prints like this
        RecordingStore.instances.append(self)

    def add(self, triple, context=None, quoted=False):
        self.added.append(triple)

    def close(self, commit_pending_transaction=False):
        self.closed_with = commit_pending_transaction


def test_write_graph_groups_by_subject_and_keeps_stdout_clean(monkeypatch, capsys):
    monkeypatch.setattr(ri, "Neo4jStore", RecordingStore)
    loaded = ri.load_graph(str(FIXTURES / "root.ttl"), catalog=ri.Catalog(FIXTURES, scan=False))
    settings = Settings(uri="neo4j://localhost", username="u", password="p")
    n = ri.write_graph(loaded.graph, settings, build_prefixes())
    store = RecordingStore.instances[-1]
    assert n == len(loaded.graph) == len(store.added)
    assert store.closed_with is True
    subjects = [t[0] for t in store.added]
    # each subject appears in one contiguous run
    runs = [s for i, s in enumerate(subjects) if i == 0 or subjects[i - 1] != s]
    assert len(runs) == len(set(subjects))
    out = capsys.readouterr()
    assert out.out == ""
    assert "store says hello" in out.err
    assert store.config.auth_data["pwd"] == "p"
    assert sys.stdout is not None


def test_import_graph_stops_before_connecting_on_multivalue_conflict(monkeypatch):
    def no_db(*a, **k):
        raise AssertionError("must not connect")

    monkeypatch.setattr(ri, "create_driver", no_db)
    g = Graph()
    s = URIRef("https://example.org/x")
    g.add((s, RDFS.label, Literal("a")))
    g.add((s, RDFS.label, Literal("b")))
    settings = Settings(uri="neo4j://localhost", username="u", password="p")
    from iof_gdb.storage import MultiValueError

    with pytest.raises(MultiValueError):
        ri.import_graph(g, settings)
