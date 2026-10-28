"""Command line interface (typer)."""

import typer

app = typer.Typer(help="Load IOF / BFO RDF into Neo4j Aura.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """iof-gdb command line."""
