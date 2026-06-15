# dataprism

Data governance toolkit for relational databases. Provides audit logging,
policy-as-code enforcement, and column classification (PII / PHI / sensitive
tagging) with support for PostgreSQL and planned support for MySQL, SQL Server, and Oracle.

## Status

Phase 1 complete. Phase 2 in progress.

**Shipped:**
- Audit logging (tamper-evident, append-only event log with SHA-256 hash chaining)
- Policy engine (YAML-driven rules validated against Pydantic schemas)
- Classification (regex, dictionary, and statistical classifiers; high-level `classify_table` API)
- Database adapters (`DatabaseAdapter` Protocol + `SqliteAdapter` + `PostgresAdapter`)
- Command-line interface (`dataprism table classify`, `dataprism audit verify`)

**In progress (v2):**
- Report generation

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

Every classification appends to the audit log at `audit/audit.jsonl`. Verify the chain is intact:

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
