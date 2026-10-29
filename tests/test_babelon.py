import csv
from pathlib import Path

import pytest
from rdflib import RDFS, Graph, Literal, URIRef

from iof_gdb import rdf_import, storage
from iof_gdb.babelon import BabelonError, parse_babelon
from iof_gdb.prefixes import build_prefixes, expand
from iof_gdb.profile import load_profile

ROOT = Path(__file__).resolve().parents[1]
TRANSLATIONS = sorted((ROOT / "data" / "translations").glob("*.babelon.tsv"))
ONTOLOGIES = ROOT / "data" / "ontologies"
P = build_prefixes(domain=load_profile("iof").prefixes)
BFO_ME = URIRef("http://purl.obolibrary.org/obo/BFO_0000040")
HEADER = (
    "\t".join(
        [
            "subject_id",
            "predicate_id",
            "source_value",
            "translation_language",
            "translation_value",
            "translation_type",
        ]
    )
    + "\n"
)


def test_parse_babelon_rows():
    tsv = (
        HEADER
        + "bfo:BFO_0000040\trdfs:label\tmaterial entity\tko\t물질적 존재자\tHumanCurated\n"
        + "bfo:BFO_0000030\trdfs:label\tobject\tko\t\tMachineTranslated\n"  # empty: skipped
        + "bfo:BFO_0000023\trdfs:label\trole\tko\t역할\tMachineTranslated\n"
    )
    g = parse_babelon(tsv.encode("utf-8-sig"), P)
    assert (BFO_ME, RDFS.label, Literal("물질적 존재자", lang="ko")) in g
    assert len(g) == 2
    assert len(parse_babelon(tsv, P, curated_only=True)) == 1


@pytest.mark.parametrize(
    "tsv, message",
    [
        ("subject_id\ttranslation_value\n", "missing Babelon columns"),
        (HEADER + "nope:x\trdfs:label\tx\tko\t엑스\t\n", ":2: Unknown prefix"),
        (HEADER + "bfo:BFO_1\trdfs:label\tx\t\t엑스\t\n", "translation_language is empty"),
    ],
)
def test_parse_babelon_errors(tsv, message):
    with pytest.raises(BabelonError, match=message):
        parse_babelon(tsv, P)


def test_tsv_is_imported_as_language_labels(tmp_path, monkeypatch):
    monkeypatch.setenv("IOF_GDB_PROFILE", "iof")
    path = tmp_path / "x.babelon.tsv"
    path.write_text(HEADER + "bfo:BFO_0000040\trdfs:label\tmaterial entity\tko\t물질적 존재자\t\n")
    loaded = rdf_import.load_graph(str(path), catalog=rdf_import.Catalog(tmp_path, scan=False))
    prepared = storage.prepare(loaded.graph, ["en", "ko"], [])
    # only the @ko property: the English rdfs:label already in the DB is left alone
    assert set(prepared.graph) == {
        (BFO_ME, URIRef(str(RDFS.label) + "@ko"), Literal("물질적 존재자"))
    }


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda p: p.name)
def test_translation_files_are_valid(path):
    rows = list(csv.DictReader(path.open(encoding="utf-8"), delimiter="\t"))
    assert rows
    keys = [(r["subject_id"], r["predicate_id"], r["translation_language"]) for r in rows]
    assert len(keys) == len(set(keys)), "duplicate translation rows"
    g = parse_babelon(path.read_bytes(), P, source=path.name)
    assert len(g) == sum(1 for r in rows if r["translation_value"].strip())


@pytest.mark.parametrize("path", TRANSLATIONS, ids=lambda p: p.name)
def test_translations_match_current_source_labels(path):
    """source_value must equal the current English label (catches upstream renames)."""
    files = [f for f in ONTOLOGIES.glob("*") if f.suffix in (".owl", ".rdf", ".ttl")]
    if not files:
        pytest.skip("ontology copies not downloaded (uv run iof-gdb fetch)")
    onto = Graph()
    for f in files:
        onto.parse(f, format=rdf_import.guess_format(str(f)))
    stale = []
    for r in csv.DictReader(path.open(encoding="utf-8"), delimiter="\t"):
        subject = URIRef(expand(r["subject_id"], P))
        predicate = URIRef(expand(r["predicate_id"], P))
        current = {str(o) for o in onto.objects(subject, predicate) if isinstance(o, Literal)}
        if r["source_value"] not in current:
            stale.append((r["subject_id"], r["source_value"], sorted(current)))
    assert not stale, f"source labels changed upstream: {stale[:5]}"
