"""MCP server (stdio) for RDF import, ontology exploration and knowledge input."""

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("iof-gdb")


def main() -> None:
    mcp.run("stdio")
