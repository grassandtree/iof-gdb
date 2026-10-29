"""Profiles: everything that depends on *which* ontology is in the graph.

A profile (TOML) holds the domain prefixes, which rdf:types count as classes, which literal
properties hold labels / synonyms / definitions, the ontologies ``fetch`` downloads by default
and extra MCP instructions. Built-in profiles live in ``iof_gdb/profiles/``; select one with
IOF_GDB_PROFILE (a built-in name such as ``iof`` / ``generic``, or a path to a .toml file).

``GraphNames`` turns standard OWL/RDFS IRIs and the profile's vocabulary into the names
rdflib-neo4j stores (``owl__Class``, ``rdfs__label``, ...), so queries never hard-code them.
"""

from __future__ import annotations

import functools
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from rdflib import OWL, RDF, RDFS

from .config import DEFAULT_PROFILE, Settings, load_env
from .prefixes import W3C_PREFIXES, build_prefixes, expand, is_valid_prefix_name, shorten

BUILTIN_DIR = "profiles"


class ProfileError(ValueError):
    """A profile file that is missing, malformed or inconsistent."""


@dataclass(frozen=True)
class Profile:
    name: str
    description: str
    prefixes: dict[str, str]
    class_types: tuple[str, ...]  # full IRIs
    label_properties: tuple[str, ...]
    synonym_properties: tuple[str, ...]
    definition_properties: tuple[str, ...]
    fetch_iris: tuple[str, ...]
    instructions: str
    source: str  # file it was loaded from


def builtin_profiles() -> list[str]:
    folder = resources.files("iof_gdb").joinpath(BUILTIN_DIR)
    return sorted(
        p.name.removesuffix(".toml") for p in folder.iterdir() if p.name.endswith(".toml")
    )


def _read(name_or_path: str) -> tuple[dict[str, Any], str]:
    if name_or_path.endswith(".toml") or "/" in name_or_path or "\\" in name_or_path:
        path = Path(name_or_path).expanduser()
        if not path.is_file():
            raise ProfileError(f"Profile file not found: {path}")
        return tomllib.loads(path.read_text(encoding="utf-8")), str(path)
    res = resources.files("iof_gdb").joinpath(BUILTIN_DIR, f"{name_or_path}.toml")
    if not res.is_file():
        raise ProfileError(
            f"Unknown profile {name_or_path!r}. Built-in: {', '.join(builtin_profiles())}; "
            "or give a path to a .toml file."
        )
    return tomllib.loads(res.read_text(encoding="utf-8")), f"builtin:{name_or_path}"


def parse_profile(data: Mapping[str, Any], source: str = "<memory>") -> Profile:
    """Validate a profile dict (as read from TOML)."""
    prefixes = {str(k): str(v) for k, v in (data.get("prefixes") or {}).items()}
    for name, ns in prefixes.items():
        if not is_valid_prefix_name(name):
            raise ProfileError(f"{source}: prefix name {name!r} must be letters/digits only")
        if name in W3C_PREFIXES and W3C_PREFIXES[name] != ns:
            raise ProfileError(f"{source}: prefix {name!r} is reserved for {W3C_PREFIXES[name]}")
    all_prefixes = build_prefixes(domain=prefixes)
    vocab = data.get("vocabulary") or {}

    def iris(key: str, required: bool = True) -> tuple[str, ...]:
        values = vocab.get(key, [])
        if required and not values:
            raise ProfileError(f"{source}: [vocabulary] {key} must list at least one IRI")
        out = []
        for v in values:
            try:
                iri = expand(str(v), all_prefixes)
            except ValueError as e:
                raise ProfileError(f"{source}: [vocabulary] {key}: {e}") from None
            if shorten(iri, all_prefixes) is None:
                raise ProfileError(
                    f"{source}: [vocabulary] {key}: {iri} has no prefix; add its namespace "
                    "to [prefixes]"
                )
            out.append(iri)
        return tuple(out)

    return Profile(
        name=str(data.get("name") or Path(source).stem),
        description=str(data.get("description") or ""),
        prefixes=prefixes,
        class_types=iris("class_types"),
        label_properties=iris("label_properties"),
        synonym_properties=iris("synonym_properties", required=False),
        definition_properties=iris("definition_properties", required=False),
        fetch_iris=tuple(str(i) for i in (data.get("fetch") or {}).get("default_iris", [])),
        instructions=str((data.get("mcp") or {}).get("instructions") or "").strip(),
        source=source,
    )


@functools.cache
def load_profile(name_or_path: str) -> Profile:
    try:
        data, source = _read(name_or_path)
    except tomllib.TOMLDecodeError as e:
        raise ProfileError(f"{name_or_path}: invalid TOML: {e}") from None
    return parse_profile(data, source)


def active_profile_name() -> str:
    """IOF_GDB_PROFILE from the environment or .env, else the default (``iof``)."""
    load_env()
    return os.getenv("IOF_GDB_PROFILE") or DEFAULT_PROFILE


def profile_for(settings: Settings) -> Profile:
    return load_profile(settings.profile)


def prefix_map(settings: Settings, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """W3C + profile + instance namespace (+ extra, e.g. prefixes stored in the database)."""
    return build_prefixes(settings.instance_ns, extra=extra, domain=profile_for(settings).prefixes)


def _q(name: str) -> str:
    """Backtick-quote a label / property / relationship name for Cypher."""
    return "`" + name.replace("`", "``") + "`"


@dataclass(frozen=True)
class GraphNames:
    """Stored names (``prefix__local``) for the standard terms and the profile's vocabulary."""

    class_labels: tuple[str, ...]
    label_props: tuple[str, ...]
    synonym_props: tuple[str, ...]
    definition_props: tuple[str, ...]
    sub_class_of: str
    equivalent_class: str
    domain: str
    range: str
    restriction: str
    on_property: str
    restriction_values: tuple[str, ...]  # someValuesFrom, allValuesFrom, hasValue, onClass
    cardinalities: tuple[str, ...]
    intersection_of: str
    union_of: str
    first: str
    rest: str
    object_property: str
    datatype_property: str
    annotation_property: str

    @classmethod
    def from_profile(cls, profile: Profile) -> GraphNames:
        prefixes = build_prefixes(domain=profile.prefixes)

        def s(iri: Any) -> str:
            name = shorten(str(iri), prefixes)
            if name is None:  # only possible for the W3C terms if W3C_PREFIXES changed
                raise ProfileError(f"No prefix for {iri}")
            return name

        return cls(
            class_labels=tuple(s(i) for i in profile.class_types),
            label_props=tuple(s(i) for i in profile.label_properties),
            synonym_props=tuple(s(i) for i in profile.synonym_properties),
            definition_props=tuple(s(i) for i in profile.definition_properties),
            sub_class_of=s(RDFS.subClassOf),
            equivalent_class=s(OWL.equivalentClass),
            domain=s(RDFS.domain),
            range=s(RDFS.range),
            restriction=s(OWL.Restriction),
            on_property=s(OWL.onProperty),
            restriction_values=tuple(
                s(i) for i in (OWL.someValuesFrom, OWL.allValuesFrom, OWL.hasValue, OWL.onClass)
            ),
            cardinalities=tuple(
                s(i)
                for i in (
                    OWL.cardinality,
                    OWL.minCardinality,
                    OWL.maxCardinality,
                    OWL.qualifiedCardinality,
                    OWL.minQualifiedCardinality,
                    OWL.maxQualifiedCardinality,
                )
            ),
            intersection_of=s(OWL.intersectionOf),
            union_of=s(OWL.unionOf),
            first=s(RDF.first),
            rest=s(RDF.rest),
            object_property=s(OWL.ObjectProperty),
            datatype_property=s(OWL.DatatypeProperty),
            annotation_property=s(OWL.AnnotationProperty),
        )

    @property
    def property_kinds(self) -> dict[str, str]:
        return {
            self.object_property: "object",
            self.datatype_property: "datatype",
            self.annotation_property: "annotation",
        }

    # Cypher fragments -------------------------------------------------------------------

    @staticmethod
    def q(name: str) -> str:
        return _q(name)

    def has_label(self, var: str, labels: tuple[str, ...] | list[str]) -> str:
        return "(" + " OR ".join(f"{var}:{_q(label)}" for label in labels) + ")"

    def is_class(self, var: str) -> str:
        return self.has_label(var, self.class_labels)

    def coalesce(self, var: str, props: tuple[str, ...]) -> str:
        if not props:
            return "null"
        return "coalesce(" + ", ".join(f"{var}.{_q(p)}" for p in props) + ")"

    def label(self, var: str) -> str:
        return self.coalesce(var, self.label_props)

    def definition(self, var: str) -> str:
        return self.coalesce(var, self.definition_props)

    def rel_types(self, *names: str) -> str:
        return "|".join(_q(n) for n in names)


@functools.cache
def names_for(name_or_path: str) -> GraphNames:
    return GraphNames.from_profile(load_profile(name_or_path))
