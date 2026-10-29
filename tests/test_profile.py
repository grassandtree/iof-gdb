import pytest

from iof_gdb import ontology
from iof_gdb.profile import (
    GraphNames,
    ProfileError,
    builtin_profiles,
    load_profile,
    names_for,
    parse_profile,
)

from .fakes import FakeDriver

SCHEMA_LIKE = """
name = "schema-like"
[prefixes]
sdo = "https://schema.org/"
[vocabulary]
class_types = ["rdfs:Class", "skos:Concept"]
label_properties = ["rdfs:label", "sdo:name"]
definition_properties = ["sdo:description"]
[fetch]
default_iris = []
"""


def test_builtin_profiles_load():
    assert {"iof", "generic"} <= set(builtin_profiles())
    iof = load_profile("iof")
    assert iof.prefixes["bfo"] == "http://purl.obolibrary.org/obo/"
    assert iof.definition_properties[0].endswith("/annotation/naturalLanguageDefinition")
    assert iof.fetch_iris and "IOF Core" in iof.description
    assert iof.languages == ("en", "ko")
    assert any(i.endswith("/annotation/firstOrderLogicAxiom") for i in iof.multivalued)
    generic = load_profile("generic")
    assert generic.prefixes == {} and generic.fetch_iris == ()


def test_profile_from_file_drives_names_and_queries(tmp_path):
    path = tmp_path / "schema-like.toml"
    path.write_text(SCHEMA_LIKE)
    profile = load_profile(str(path))
    names = GraphNames.from_profile(profile)
    assert names.class_labels == ("rdfs__Class", "skos__Concept")
    assert names.label_props == ("rdfs__label", "sdo__name")
    assert names.definition_props == ("sdo__description",)
    # standard OWL/RDFS terms do not depend on the profile
    assert names.sub_class_of == "rdfs__subClassOf" == names_for("iof").sub_class_of

    driver = FakeDriver(lambda q, p: [])
    ontology.search_classes(driver, "pump", names=names)
    query = driver.queries[0][0]
    assert "n:`rdfs__Class` OR n:`skos__Concept`" in query
    assert "owl__Class" not in query and "iofav" not in query
    assert "coalesce(n.`sdo__description`)" in query


@pytest.mark.parametrize(
    "toml, message",
    [
        (
            '[prefixes]\n"bad-name" = "https://x.example/"\n[vocabulary]\nclass_types=["owl:Class"]\n'
            'label_properties=["rdfs:label"]',
            "letters/digits",
        ),
        (
            '[prefixes]\nowl = "https://x.example/"\n[vocabulary]\nclass_types=["owl:Class"]\n'
            'label_properties=["rdfs:label"]',
            "reserved",
        ),
        ('[vocabulary]\nclass_types=[]\nlabel_properties=["rdfs:label"]', "at least one"),
        (
            '[vocabulary]\nclass_types=["nope:Class"]\nlabel_properties=["rdfs:label"]',
            "Unknown prefix",
        ),
        (
            '[vocabulary]\nclass_types=["https://x.example/v/Class"]\n'
            'label_properties=["rdfs:label"]',
            "has no prefix",
        ),
    ],
)
def test_invalid_profiles(toml, message):
    import tomllib

    with pytest.raises(ProfileError, match=message):
        parse_profile(tomllib.loads(toml), "test.toml")


def test_missing_and_broken_profile_files(tmp_path):
    with pytest.raises(ProfileError, match="not found"):
        load_profile(str(tmp_path / "missing.toml"))
    broken = tmp_path / "broken.toml"
    broken.write_text("name = ")
    with pytest.raises(ProfileError, match="invalid TOML"):
        load_profile(str(broken))
