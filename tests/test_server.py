import asyncio
import json
import os
import sys

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from iof_gdb import ontology, server

EXPECTED_TOOLS = {
    "import_rdf",
    "import_rdf_text",
    "search_classes",
    "describe_class",
    "search_properties",
    "add_individual",
    "add_relation",
    "graph_stats",
}


def run(coro):
    return asyncio.run(coro)


def test_tools_and_resource_in_process(monkeypatch):
    monkeypatch.setattr(server, "_prefixes", lambda: {"bfo": "http://purl.obolibrary.org/obo/"})
    monkeypatch.setattr(server, "_driver", lambda: object())
    monkeypatch.setattr(
        ontology,
        "describe_class",
        lambda driver, iri, **kw: {"iri": iri} if "0040" in iri else None,
    )

    async def go():
        async with Client(server.mcp, mode="legacy") as client:
            tools = {t.name for t in (await client.list_tools()).tools}
            ok = await client.call_tool("describe_class", {"iri_or_curie": "bfo:BFO_0000040"})
            missing = await client.call_tool("describe_class", {"iri_or_curie": "bfo:BFO_9"})
            bad = await client.call_tool("describe_class", {"iri_or_curie": "nope:x"})
            res = await client.read_resource("iof://prefixes")
            prof = await client.read_resource("iof://profile")
            return tools, ok, missing, bad, res, prof

    tools, ok, missing, bad, res, prof = run(go())
    assert tools == EXPECTED_TOOLS
    assert not ok.is_error
    assert ok.structured_content == {"iri": "http://purl.obolibrary.org/obo/BFO_0000040"}
    assert missing.is_error and "search_classes" in missing.content[0].text
    assert bad.is_error and "Unknown prefix" in bad.content[0].text
    assert json.loads(res.contents[0].text)["bfo"] == "http://purl.obolibrary.org/obo/"
    profile = json.loads(prof.contents[0].text)
    assert profile["name"] == "iof" and "owl__Class" in profile["class_labels"]


def test_stdio_server_starts_with_clean_stdout(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("NEO4J_")}
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "iof_gdb.server"], env=env, cwd=str(tmp_path)
    )

    async def go():
        async with Client(params, mode="legacy") as client:
            tools = {t.name for t in (await client.list_tools()).tools}
            res = await client.read_resource("iof://prefixes")
            return tools, json.loads(res.contents[0].text)

    tools, prefixes = run(go())
    assert tools == EXPECTED_TOOLS
    assert prefixes["iofconstr"] == "https://spec.industrialontologies.org/ontology/construct/"
