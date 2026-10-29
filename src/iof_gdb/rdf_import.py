"""RDF/OWL import into Neo4j via rdflib-neo4j.

Flow: parse into an in-memory rdflib Graph -> (optionally) follow owl:imports, preferring local
copies listed in data/ontologies/catalog-v001.xml -> check vocabulary namespaces against the
prefix map -> stream the triples into ``Graph(store=Neo4jStore(config))`` -> close/commit.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from rdflib import OWL, RDF, Graph, URIRef
from rdflib_neo4j import Neo4jStore

from . import db, storage
from .babelon import parse_babelon
from .config import (
    DEFAULT_INSTANCE_NS,
    ONTOLOGY_DIR,
    Settings,
    create_driver,
    create_store_config,
    load_settings,
)
from .prefixes import build_prefixes, find_unregistered, generate_prefixes, shorten
from .profile import active_profile_name, load_profile, prefix_map, profile_for

FORMAT_BY_SUFFIX = {
    ".ttl": "turtle",
    ".turtle": "turtle",
    ".owl": "xml",
    ".rdf": "xml",
    ".xml": "xml",
    ".nt": "nt",
    ".n3": "n3",
    ".jsonld": "json-ld",
    ".json": "json-ld",
    ".tsv": "babelon",  # translation table, see babelon.py
}
FORMAT_ALIASES = {
    "ttl": "turtle",
    "rdfxml": "xml",
    "rdf/xml": "xml",
    "rdf": "xml",
    "owl": "xml",
    "jsonld": "json-ld",
    "ntriples": "nt",
    "n-triples": "nt",
}
FORMAT_BY_CONTENT_TYPE = {
    "text/turtle": "turtle",
    "application/x-turtle": "turtle",
    "application/rdf+xml": "xml",
    "application/owl+xml": None,  # OWL/XML is not RDF; let sniffing fail loudly
    "application/n-triples": "nt",
    "text/n3": "n3",
    "application/ld+json": "json-ld",
}
EXTENSION_BY_FORMAT = {
    "turtle": ".ttl",
    "xml": ".rdf",
    "nt": ".nt",
    "n3": ".n3",
    "json-ld": ".jsonld",
}
ACCEPT = (
    "text/turtle, application/rdf+xml;q=0.9, application/ld+json;q=0.8, "
    "application/n-triples;q=0.7, */*;q=0.1"
)
CATALOG_FILE = "catalog-v001.xml"
CATALOG_NS = "urn:oasis:names:tc:entity:xmlns:xml:catalog"


class UnregisteredNamespaceError(ValueError):
    def __init__(self, namespaces: Iterable[str]):
        self.namespaces = sorted(namespaces)
        listed = "\n  ".join(self.namespaces)
        super().__init__(
            "These vocabulary namespaces have no prefix, so the SHORTEN strategy cannot store "
            f"them:\n  {listed}\nAdd them to the profile's [prefixes] or re-run with auto prefixes "
            "(CLI: --auto-prefix, MCP: auto_prefix=true)."
        )


# --------------------------------------------------------------------------- formats / reading


def normalize_format(fmt: str | None) -> str | None:
    if not fmt:
        return None
    fmt = fmt.strip().lower()
    return FORMAT_ALIASES.get(fmt, fmt)


def guess_format(location: str) -> str | None:
    """Format from the file extension (path or URL), or None."""
    path = urllib.parse.urlparse(location).path if is_url(location) else location
    return FORMAT_BY_SUFFIX.get(Path(path).suffix.lower())


def sniff_format(data: bytes) -> str:
    head = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if head.startswith((b"<?xml", b"<rdf:rdf", b"<!doctype", b"<rdf")):
        return "xml"
    if head.startswith((b"{", b"[")):
        return "json-ld"
    return "turtle"


def is_url(source: str) -> bool:
    return urllib.parse.urlparse(source).scheme in ("http", "https")


@dataclass
class Document:
    """Raw RDF content plus where it came from."""

    location: str  # file path or final URL
    data: bytes
    format: str
    base: str | None = None  # base IRI for relative references


def fetch_url(url: str, timeout: float = 60.0) -> tuple[bytes, str, str | None]:
    """GET ``url`` with RDF content negotiation. Returns (data, final_url, content_type)."""
    req = urllib.request.Request(url, headers={"Accept": ACCEPT, "User-Agent": "iof-gdb"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (http/https only)
        ctype = resp.headers.get_content_type() if resp.headers else None
        return resp.read(), resp.geturl(), ctype


Fetcher = Callable[[str], tuple[bytes, str, str | None]]


def read_document(source: str, fmt: str | None = None, fetch: Fetcher = fetch_url) -> Document:
    fmt = normalize_format(fmt)
    if is_url(source):
        data, final_url, ctype = fetch(source)
        fmt = (
            fmt
            or FORMAT_BY_CONTENT_TYPE.get(ctype or "")
            or guess_format(final_url)
            or guess_format(source)
            or sniff_format(data)
        )
        return Document(location=final_url, data=data, format=fmt, base=source)
    path = Path(source).expanduser()
    data = path.read_bytes()
    return Document(
        location=str(path), data=data, format=fmt or guess_format(str(path)) or sniff_format(data)
    )


def parse_document(doc: Document) -> Graph:
    base = doc.base or (
        doc.location if is_url(doc.location) else Path(doc.location).resolve().as_uri()
    )
    if doc.format == "babelon":
        return parse_babelon(doc.data, _active_prefixes(), source=doc.location)
    g = Graph()
    g.parse(data=doc.data, format=doc.format, publicID=base)
    return g


def _active_prefixes() -> dict[str, str]:
    """Prefixes for CURIEs in non-RDF inputs (Babelon): W3C + active profile + inst:."""
    profile = load_profile(active_profile_name())
    return build_prefixes(
        os.getenv("IOF_GDB_INSTANCE_NS") or DEFAULT_INSTANCE_NS, domain=profile.prefixes
    )


def ontology_iris(graph: Graph) -> list[str]:
    """Declared owl:Ontology IRIs and their owl:versionIRI values."""
    iris: list[str] = []
    for onto in graph.subjects(RDF.type, OWL.Ontology):
        if isinstance(onto, URIRef):
            iris.append(str(onto))
        iris.extend(str(v) for v in graph.objects(onto, OWL.versionIRI) if isinstance(v, URIRef))
    return iris


def import_iris(graph: Graph) -> list[str]:
    return sorted({str(o) for o in graph.objects(None, OWL.imports) if isinstance(o, URIRef)})


# --------------------------------------------------------------------------- local catalog


class Catalog:
    """IRI -> local file mapping (Protégé-style OASIS XML catalog, plus a declared-IRI scan)."""

    def __init__(self, directory: Path = ONTOLOGY_DIR, scan: bool = True):
        self.directory = Path(directory)
        self.entries: dict[str, Path] = {}
        self._scan = scan
        self._scanned = False
        catalog = self.directory / CATALOG_FILE
        if catalog.exists():
            root = ET.parse(catalog).getroot()
            for el in root.iter(f"{{{CATALOG_NS}}}uri"):
                name, uri = el.get("name"), el.get("uri")
                if name and uri:
                    self.entries[name] = (self.directory / uri).resolve()

    def _scan_files(self) -> None:
        self._scanned = True
        if not self.directory.is_dir():
            return
        for path in sorted(self.directory.iterdir()):
            if path.suffix.lower() not in FORMAT_BY_SUFFIX or path.name == CATALOG_FILE:
                continue
            if path.resolve() in self.entries.values():
                continue
            try:
                g = parse_document(read_document(str(path)))
            except Exception:  # noqa: BLE001 - unreadable files are just not indexed
                continue
            for iri in ontology_iris(g):
                self.entries.setdefault(iri, path.resolve())

    def resolve(self, iri: str) -> Path | None:
        candidates = {iri, iri.rstrip("/"), iri + "/"}
        for c in candidates:
            if c in self.entries and self.entries[c].exists():
                return self.entries[c]
        if self._scan and not self._scanned:
            self._scan_files()
            return self.resolve(iri)
        return None

    def write(self) -> Path:
        ET.register_namespace("", CATALOG_NS)
        root = ET.Element(f"{{{CATALOG_NS}}}catalog", {"prefer": "public"})
        for i, (name, path) in enumerate(sorted(self.entries.items()), 1):
            ET.SubElement(
                root,
                f"{{{CATALOG_NS}}}uri",
                {"id": f"iof-gdb-{i}", "name": name, "uri": path.name},
            )
        ET.indent(root)
        out = self.directory / CATALOG_FILE
        ET.ElementTree(root).write(out, encoding="UTF-8", xml_declaration=True)
        return out


# --------------------------------------------------------------------------- loading


@dataclass
class LoadedGraph:
    graph: Graph
    ontologies: list[str] = field(default_factory=list)  # declared ontology IRIs
    sources: list[str] = field(default_factory=list)  # files / URLs actually read
    unresolved: dict[str, str] = field(default_factory=dict)  # import IRI -> error


def load_graph(
    source: str,
    fmt: str | None = None,
    follow_imports: bool = False,
    catalog: Catalog | None = None,
    fetch: Fetcher = fetch_url,
) -> LoadedGraph:
    """Parse ``source`` (path or URL) into one in-memory Graph, optionally with its imports."""
    catalog = catalog if catalog is not None else Catalog()
    result = LoadedGraph(graph=Graph())
    seen: set[str] = set()

    def add(doc: Document) -> list[str]:
        g = parse_document(doc)
        result.graph += g
        result.sources.append(doc.location)
        declared = ontology_iris(g)
        result.ontologies.extend(i for i in declared if i not in result.ontologies)
        seen.update(declared)
        return import_iris(g)

    queue = add(read_document(source, fmt, fetch))
    while follow_imports and queue:
        iri = queue.pop(0)
        if iri in seen:
            continue
        seen.add(iri)
        local = catalog.resolve(iri)
        try:
            doc = read_document(str(local)) if local else read_document(iri, fetch=fetch)
        except Exception as e:  # noqa: BLE001 - report and keep going
            result.unresolved[iri] = f"{type(e).__name__}: {e}"
            continue
        queue.extend(i for i in add(doc) if i not in seen)
    return result


def parse_text(content: str, fmt: str = "turtle") -> Graph:
    g = Graph()
    g.parse(data=content, format=normalize_format(fmt) or "turtle")
    return g


# --------------------------------------------------------------------------- writing to Neo4j


@dataclass
class ImportSummary:
    triples: int
    ontologies: list[str]
    sources: list[str]
    seconds: float
    new_prefixes: dict[str, str] = field(default_factory=dict)
    unresolved_imports: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "triples": self.triples,
            "ontologies": self.ontologies,
            "sources": self.sources,
            "seconds": round(self.seconds, 2),
            "new_prefixes": self.new_prefixes,
            "unresolved_imports": self.unresolved_imports,
        }


def resolve_prefixes(
    graph: Graph, known: dict[str, str], auto_prefix: bool
) -> tuple[dict[str, str], dict[str, str]]:
    """Return (prefixes to use, newly generated ones). Raises if namespaces are missing."""
    missing = find_unregistered(graph, known)
    if not missing:
        return known, {}
    if not auto_prefix:
        raise UnregisteredNamespaceError(missing)
    new = generate_prefixes(missing, known)
    return {**known, **new}, new


def write_graph(
    graph: Graph,
    settings: Settings,
    prefixes: dict[str, str],
    array_predicates: Iterable[str] = (),
) -> int:
    """Stream ``graph`` into Neo4j with rdflib-neo4j. Returns the number of triples sent."""
    config = create_store_config(settings, prefixes, array_predicates)
    count = 0
    # rdflib-neo4j prints progress to stdout; keep stdout clean (MCP stdio uses it).
    with contextlib.redirect_stdout(sys.stderr):
        store = Neo4jStore(config=config)  # opens the store and checks the constraint
        try:
            neo4j_graph = Graph(store=store)
            # Neo4jStore buffers one subject at a time, so feed triples grouped by subject.
            for s in graph.subjects(unique=True):
                for p, o in graph.predicate_objects(s):
                    neo4j_graph.add((s, p, o))
                    count += 1
            neo4j_graph.close(True)  # commit pending batches
        finally:
            if store.driver is not None:
                store.driver.close()
    return count


def import_graph(
    graph: Graph, settings: Settings | None = None, auto_prefix: bool = False
) -> tuple[int, dict[str, str]]:
    """Apply the storage policy, check namespaces, write ``graph`` to Neo4j.

    Returns (triples written, new prefixes). Nothing is written when the namespace or
    multi-value checks fail.
    """
    settings = settings or load_settings()
    profile = profile_for(settings)
    prepared = storage.prepare(graph, profile.languages, profile.multivalued)
    driver = create_driver(settings)
    try:
        known = prefix_map(settings, extra=db.load_prefix_defs(driver))
        prefixes, new = resolve_prefixes(prepared.graph, known, auto_prefix)
        array_props = sorted(
            filter(None, (shorten(p, prefixes) for p in prepared.array_predicates))
        )
        subjects = sorted({str(s) for s in prepared.graph.subjects(unique=True)})
        db.wrap_scalars_in_lists(driver, subjects, array_props)
        count = write_graph(prepared.graph, settings, prefixes, prepared.array_predicates)
        db.save_prefix_defs(driver, prefixes)
    finally:
        driver.close()
    return count, new


def import_source(
    src: str,
    fmt: str | None = None,
    follow_imports: bool = False,
    *,
    auto_prefix: bool = False,
    settings: Settings | None = None,
    catalog: Catalog | None = None,
) -> ImportSummary:
    """Import a file path or URL (optionally with its owl:imports closure) into Neo4j."""
    start = time.perf_counter()
    loaded = load_graph(src, fmt, follow_imports, catalog)
    count, new = import_graph(loaded.graph, settings, auto_prefix)
    return ImportSummary(
        triples=count,
        ontologies=loaded.ontologies,
        sources=loaded.sources,
        seconds=time.perf_counter() - start,
        new_prefixes=new,
        unresolved_imports=loaded.unresolved,
    )


def import_text(
    content: str,
    fmt: str = "turtle",
    *,
    auto_prefix: bool = False,
    settings: Settings | None = None,
) -> ImportSummary:
    """Import an RDF snippet (default Turtle)."""
    start = time.perf_counter()
    g = parse_text(content, fmt)
    count, new = import_graph(g, settings, auto_prefix)
    return ImportSummary(
        triples=count,
        ontologies=ontology_iris(g),
        sources=["<text>"],
        seconds=time.perf_counter() - start,
        new_prefixes=new,
    )


# --------------------------------------------------------------------------- fetching local copies


def _file_name_for(iri: str, final_url: str, fmt: str, taken: set[str]) -> str:
    name = urllib.parse.unquote(Path(urllib.parse.urlparse(final_url).path).name)
    if not name or Path(name).suffix.lower() not in FORMAT_BY_SUFFIX:
        parts = [p for p in urllib.parse.urlparse(iri).path.split("/") if p]
        name = (parts[-1] if parts else "ontology") + EXTENSION_BY_FORMAT.get(fmt, ".rdf")
    name = name.replace(" ", "_")
    stem, suffix, i = Path(name).stem, Path(name).suffix, 1
    while name in taken:
        name = f"{stem}-{i}{suffix}"
        i += 1
    return name


def fetch_ontologies(
    iris: Iterable[str],
    directory: Path = ONTOLOGY_DIR,
    follow_imports: bool = True,
    fetch: Fetcher = fetch_url,
) -> dict[str, Path]:
    """Download ontologies (and their imports) into ``directory`` and update the catalog.

    Every IRI that leads to a file (requested import IRI, declared ontology IRI, versionIRI)
    is recorded in catalog-v001.xml so later imports resolve locally.
    """
    directory.mkdir(parents=True, exist_ok=True)
    catalog = Catalog(directory, scan=False)
    taken = {p.name for p in directory.iterdir()}
    saved: dict[str, Path] = {}
    queue, seen = list(iris), set()
    while queue:
        iri = queue.pop(0)
        if iri in seen:
            continue
        seen.add(iri)
        doc = read_document(iri, fetch=fetch)
        g = parse_document(doc)  # validates the download before saving
        name = _file_name_for(iri, doc.location, doc.format, taken)
        existing = catalog.resolve(iri)
        path = existing or (directory / name)
        if existing is None:
            taken.add(name)
        path.write_bytes(doc.data)
        for alias in {iri, doc.location, *ontology_iris(g)}:
            catalog.entries[alias] = path.resolve()
            seen.add(alias)
        saved[iri] = path
        if follow_imports:
            queue.extend(i for i in import_iris(g) if i not in seen)
    catalog.write()
    return saved
