# dataprism

Data governance toolkit for relational databases. Provides audit logging,
policy-as-code enforcement, and column classification (PII / PHI / sensitive
tagging) with support for PostgreSQL and planned support for MySQL, SQL Server, and Oracle.

## Status

Phase 1 complete. Phase 2 in progress.

**Shipped:**
- Audit logging (tamper-evident, append-only event log with SHA-256 hash chaining)
- Policy engine (YAML-driven rules validated against Pydantic schemas)
- Classification (regex, dictionary, and statistical classifiers for PII/PHI)
- Database adapters (`DatabaseAdapter` Protocol + `SqliteAdapter` + `PostgresAdapter`)

**In progress (v2):**
- High-level API wiring adapters + classification + audit
- CLI scaffolding
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

## Architecture

For a deep dive on the design, see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
It covers the four subsystems (audit, policy, classification, adapters),
the cross-cutting design principles, what is intentionally deferred to
v3 and beyond, and a glossary.

If you're new to the codebase, the architecture document also includes a
suggested reading order for the source files.

## License

MIT
