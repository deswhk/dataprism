"""Command-line interface for dataprism.

The entry point declared in pyproject.toml is `dataprism.cli:main`,
so `main` must be importable directly from this package. We re-export
it here from cli.main where the typer app actually lives.

Subcommands are organized into groups under the top-level `dataprism`
command:

    dataprism table classify ...    # classify columns in a table
    dataprism audit verify          # verify the audit log's hash chain

For internal CLI components, see:
    cli.paths    - project root, audit log path, policy path resolution
    cli.adapters - DSN normalization and adapter selection
    cli.render   - text and JSON renderers for classification reports
    cli.main     - typer app and command implementations
"""

from dataprism.cli.main import main

__all__ = ["main"]
