# dataprism

Data governance toolkit for relational databases. Provides audit logging,
policy-as-code enforcement, column classification (PII / PHI / sensitive
tagging), and data quality checks across PostgreSQL, SQL Server, and Oracle.

## Status

Early development. Foundational components (audit log, policy engine) under
construction.

## Planned scope (v1)

- **Audit logging** — tamper-evident, append-only event log
- **Policy engine** — YAML-driven rules validated against Pydantic schemas
- **Classification** — regex, dictionary, and statistical classifiers for PII/PHI
- **Quality checks** — completeness, uniqueness, range, freshness, referential

Encryption (column-level envelope encryption with key rotation) is planned
for v2.

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

## License

MIT