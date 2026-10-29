"""Babelon translation tables (TSV) -> RDF.

Babelon (https://github.com/monarch-initiative/babelon) is the OBO community's simple format
for ontology translations: one row per translated value with ``subject_id``, ``predicate_id``,
``translation_language`` and ``translation_value`` (plus source value and review metadata).
Each row with a translation becomes ``<subject> <predicate> "translation"@lang``; the storage
policy then files it under ``predicate@lang`` (e.g. ``rdfs__label@ko``).

``subject_id`` / ``predicate_id`` may be full IRIs or CURIEs using the active profile's
prefixes (e.g. ``bfo:BFO_0000040``, ``rdfs:label``).
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping

from rdflib import Graph, Literal, URIRef

from .prefixes import expand

REQUIRED_COLUMNS = ("subject_id", "predicate_id", "translation_language", "translation_value")


class BabelonError(ValueError):
    """A malformed Babelon table."""


def parse_babelon(
    data: bytes | str,
    prefixes: Mapping[str, str],
    *,
    curated_only: bool = False,
    source: str = "<babelon>",
) -> Graph:
    """Translations as triples. Rows without a translation are skipped.

    ``curated_only`` keeps only rows whose translation_type is ``HumanCurated``.
    """
    text = data.decode("utf-8-sig") if isinstance(data, bytes) else data
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise BabelonError(f"{source}: missing Babelon columns: {', '.join(missing)}")
    g = Graph()
    for line, row in enumerate(reader, start=2):
        value = (row.get("translation_value") or "").strip()
        if not value:
            continue
        if curated_only and (row.get("translation_type") or "").strip() != "HumanCurated":
            continue
        lang = (row.get("translation_language") or "").strip()
        if not lang:
            raise BabelonError(f"{source}:{line}: translation_language is empty")
        try:
            subject = expand(row["subject_id"].strip(), prefixes)
            predicate = expand(row["predicate_id"].strip(), prefixes)
        except ValueError as e:
            raise BabelonError(f"{source}:{line}: {e}") from None
        g.add((URIRef(subject), URIRef(predicate), Literal(value, lang=lang)))
    return g
