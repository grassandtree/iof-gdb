"""Storage policy applied to a graph right before rdflib-neo4j writes it.

1. Blank nodes get deterministic ids (RGDA1 canonical labelling via rdflib.compare), so
   importing the same document again MERGEs onto the same nodes instead of duplicating them.
   The ids contain no ':' so "named resource" checks (``uri CONTAINS ':'``) keep working.
2. Language-tagged literals are kept per language: ``rdfs:label "펌프"@ko`` is written as
   predicate ``rdfs:label@ko`` -> property ``rdfs__label@ko``. Tags are reduced to the primary
   subtag (en-US -> en). The plain predicate keeps untagged values, else the values in the
   profile's first language, so existing queries on ``rdfs__label`` keep working and a later
   import of only Korean labels never overwrites it.
3. Predicates listed as multivalued are written as arrays; any other predicate with several
   literal values on one subject stops the import before anything is written (Neo4j would keep
   only one of them).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.compare import to_canonical_graph

LANG_SEP = "@"
_LANG_SUFFIX = re.compile(r"^(.*)@([a-z]{2,8})$")


class MultiValueError(ValueError):
    def __init__(self, conflicts: dict[str, int]):
        self.conflicts = dict(sorted(conflicts.items()))
        listed = "\n  ".join(f"{p}  ({n} subjects)" for p, n in self.conflicts.items())
        super().__init__(
            "These predicates have several literal values on the same subject, but only one "
            f"value per property would be kept:\n  {listed}\n"
            "Add them to [storage] multivalued in the profile to keep all values as arrays."
        )


def primary_subtag(lang: str) -> str:
    return lang.split("-", 1)[0].lower()


def lang_predicate(predicate: str, lang: str) -> URIRef:
    return URIRef(f"{predicate}{LANG_SEP}{primary_subtag(lang)}")


def base_predicate(predicate: str) -> str:
    """``...#label@ko`` -> ``...#label``; other IRIs unchanged."""
    m = _LANG_SUFFIX.match(str(predicate))
    return m.group(1) if m else str(predicate)


def canonical_bnodes(graph: Graph) -> Graph:
    """Same graph with blank nodes renamed by content (deterministic across parses)."""
    if not any(isinstance(x, BNode) for t in graph for x in (t[0], t[2])):
        return graph
    out = Graph()
    out += to_canonical_graph(graph)
    return out


def split_languages(graph: Graph, primary: str | None) -> Graph:
    """Move language-tagged literals to ``predicate@lang`` (see module docstring)."""
    out = Graph()
    tagged: dict[tuple, dict[str, list[Literal]]] = defaultdict(lambda: defaultdict(list))
    untagged: set[tuple] = set()
    for s, p, o in graph:
        if isinstance(o, Literal) and o.language:
            tagged[(s, p)][primary_subtag(o.language)].append(o)
        else:
            if isinstance(o, Literal):
                untagged.add((s, p))
            out.add((s, p, o))
    for (s, p), by_lang in tagged.items():
        for lang, literals in by_lang.items():
            for lit in literals:
                out.add((s, lang_predicate(p, lang), Literal(str(lit))))
        if (s, p) not in untagged and primary and primary in by_lang:
            for lit in by_lang[primary]:
                out.add((s, p, Literal(str(lit))))
    return out


def multivalue_conflicts(graph: Graph, multivalued: Iterable[str]) -> dict[str, int]:
    """Predicates (without @lang) not in ``multivalued`` that have >1 literal on a subject."""
    allowed = set(multivalued)
    counts = Counter((s, p) for s, p, o in graph if isinstance(o, Literal))
    conflicts: Counter[str] = Counter()
    for (_, p), n in counts.items():
        if n > 1 and base_predicate(p) not in allowed:
            conflicts[base_predicate(p)] += 1
    return dict(conflicts)


def array_predicates(graph: Graph, multivalued: Iterable[str]) -> set[str]:
    """Predicates in ``graph`` to store as arrays: the listed ones and their @lang variants."""
    allowed = set(multivalued)
    return {str(p) for p in graph.predicates(unique=True) if base_predicate(p) in allowed}


@dataclass
class PreparedGraph:
    graph: Graph
    array_predicates: set[str] = field(default_factory=set)


def prepare(graph: Graph, languages: Iterable[str], multivalued: Iterable[str]) -> PreparedGraph:
    """Apply the storage policy. Raises MultiValueError before anything is written."""
    languages = tuple(languages)
    multivalued = tuple(multivalued)
    g = split_languages(canonical_bnodes(graph), languages[0] if languages else None)
    conflicts = multivalue_conflicts(g, multivalued)
    if conflicts:
        raise MultiValueError(conflicts)
    return PreparedGraph(graph=g, array_predicates=array_predicates(g, multivalued))
