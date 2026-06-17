# dataprism

Data governance toolkit for relational databases. Provides audit logging,
policy-as-code enforcement, and column classification (PII / PHI / sensitive
tagging) with support for PostgreSQL and planned support for MySQL, SQL Server, and Oracle.

## Status

Phases 1 and 2 complete. Phase 3 not started.

**Shipped:**
- Audit logging (tamper-evident, append-only event log with SHA-256 hash chaining)
- Policy engine (YAML-driven rules validated against Pydantic schemas)
- Classification (regex, dictionary, statistical classifiers; `classify_table` for single tables, `classify_tables` for batches returning a self-contained `ScanReport`, `list_table_candidates` for cheap pre-scan discovery)
- Database adapters (`DatabaseAdapter` Protocol + `SqliteAdapter` + `PostgresAdapter`)
- Command-line interface (`dataprism table classify` with one-or-many `--table`, `dataprism table candidates`, `dataprism audit verify`)
- HTML scan reports (one self-contained file per classify run, written to `<project-root>/reports/`)

**Deferred to later phases:**
- Quality engine, encryption, retention pillars
- Additional database adapters (MySQL, MSSQL, Oracle)
- Multi-writer audit support

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for design rationale,
deferred decisions, and the full subsystem reference.

## Requirements

- Python >= 3.10
- pip (or uv) for dependency management

## Development setup

```powershell
git clone https://github.com/deswhk/dataprism.git
cd dataprism
pip install -e ".[dev]"
pre-commit install
pytest
```

## Usage

Once installed, dataprism provides a CLI. Set your database DSN once per session:

```powershell
$env:DATAPRISM_DSN = "postgresql://user:pass@host:5432/mydb"
```

Then classify columns in a table against a policy:

```powershell
# Policies live in config/policies/<name>.yaml
# The shipped example is config/policies/example.yaml
dataprism table classify --table users --policy example
```

Output is human-readable text by default. For machine-readable output, use `--output json`:

```powershell
dataprism table classify --table users --policy example --output json
```

To classify several tables in one run, pass a comma-separated list. Output streams a progress line per table, with a summary at the end:

```powershell
dataprism table classify --table users,orders,customers --policy example
```

To discover which tables are worth scanning before committing to a list, use `table candidates`:

```powershell
dataprism table candidates --policy example
```

The output shows all tables sorted by how many columns match the policy's name-based rules. JSON output is also available for scripting:

```powershell
dataprism table candidates --policy example --output json
```

Every classification produces two artifacts:
- A line-by-line event sequence appended to the audit log at `audit/audit.jsonl` (tamper-evident).
- A self-contained HTML scan report at `reports/<YYYY-MM-DD-HHMMSS>.html` (open in any browser; share or print as a governance artifact).

The HTML report's footer carries the scan's `scan_id`, which matches the `SCAN_STARTED`/`SCAN_COMPLETED` events in the audit log - so a reviewer can pull the report and the matching audit slice side by side.

Verify the audit chain is intact:

```powershell
dataprism audit verify
```

For full command help:

```powershell
dataprism --help
dataprism table classify --help
dataprism audit verify --help
```

## Architecture

For a deep dive on the design, see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
It covers the five subsystems (audit, policy, classification, adapters, CLI),
the cross-cutting design principles, what is intentionally deferred to
v3 and beyond, and a glossary.

If you're new to the codebase, the architecture document also includes a
suggested reading order for the source files.

## License

MIT
