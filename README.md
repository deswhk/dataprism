# dataprism

Data governance toolkit for relational databases. Provides audit logging,
policy-as-code enforcement, and column classification (PII / PHI / sensitive
tagging) with planned support for PostgreSQL, SQL Server, and Oracle.

## Status

Phase 1 in progress. Audit, policy, and classification subsystems shipped;
quality checks, database adapters, and CLI planned for Phase 2.

## Phase 1 (v1) scope

- **Audit logging** - tamper-evident, append-only event log with SHA-256 hash chaining
- **Policy engine** - YAML-driven rules validated against Pydantic schemas
- **Classification** - regex, dictionary, and statistical classifiers for PII/PHI

Quality checks, encryption, retention, database adapters, and CLI are planned
for later phases. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for
details on deferred decisions.

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
It covers the three subsystems (audit, policy, classification), the cross-cutting
design principles, what is intentionally deferred to v2 and beyond, and a glossary.

If you're new to the codebase, the architecture document also includes a
suggested reading order for the source files.

## License

MIT
