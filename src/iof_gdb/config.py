"""Connection settings: read .env / environment, build the Neo4j driver and store config."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from neo4j import Driver, GraphDatabase
from rdflib_neo4j import HANDLE_VOCAB_URI_STRATEGY, Neo4jStoreConfig

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
ONTOLOGY_DIR = DATA_DIR / "ontologies"
INSTANCE_DIR = DATA_DIR / "instances"

DEFAULT_DATABASE = "neo4j"
DEFAULT_INSTANCE_NS = "https://example.org/iof-gdb/instance/"
DEFAULT_PROFILE = "iof"


class ConfigError(RuntimeError):
    """Raised when required connection settings are missing."""


@dataclass(frozen=True)
class Settings:
    uri: str
    username: str
    password: str = field(repr=False)
    database: str = DEFAULT_DATABASE
    instance_ns: str = DEFAULT_INSTANCE_NS
    profile: str = DEFAULT_PROFILE  # built-in profile name or path to a .toml file

    def auth_data(self) -> dict[str, str]:
        """Auth dict in the shape rdflib-neo4j expects (keys: uri, database, user, pwd)."""
        return {
            "uri": self.uri,
            "database": self.database,
            "user": self.username,
            "pwd": self.password,
        }


def load_env(env_file: str | os.PathLike[str] | None = None) -> None:
    """Read .env (project root by default) into os.environ; existing variables win."""
    load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)


def load_settings(env_file: str | os.PathLike[str] | None = None) -> Settings:
    """Load settings from the environment, after reading a .env file if present.

    Values already in the environment win over the .env file.
    """
    load_env(env_file)
    missing = [k for k in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD") if not os.getenv(k)]
    if missing:
        raise ConfigError(
            f"Missing Neo4j settings: {', '.join(missing)}. Set them in .env or the environment."
        )
    return Settings(
        uri=os.environ["NEO4J_URI"],
        username=os.environ["NEO4J_USERNAME"],
        password=os.environ["NEO4J_PASSWORD"],
        database=os.getenv("NEO4J_DATABASE") or DEFAULT_DATABASE,
        instance_ns=os.getenv("IOF_GDB_INSTANCE_NS") or DEFAULT_INSTANCE_NS,
        profile=os.getenv("IOF_GDB_PROFILE") or DEFAULT_PROFILE,
    )


def create_driver(settings: Settings, **config: Any) -> Driver:
    """Create a Neo4j driver whose sessions default to ``settings.database``."""
    return GraphDatabase.driver(
        settings.uri,
        auth=(settings.username, settings.password),
        database=settings.database,
        **config,
    )


def create_store_config(settings: Settings, prefixes: dict[str, str]) -> Neo4jStoreConfig:
    """Store config for rdflib-neo4j.

    SHORTEN turns predicate / rdf:type IRIs into ``prefix__localName`` names; every namespace
    used that way must be in ``prefixes`` or the store raises ShortenStrictException.
    """
    return Neo4jStoreConfig(
        auth_data=settings.auth_data(),
        # Fresh dict each time: rdflib-neo4j uses a mutable default for this argument.
        custom_prefixes=dict(prefixes),
        handle_vocab_uri_strategy=HANDLE_VOCAB_URI_STRATEGY.SHORTEN,
        batching=True,
    )
