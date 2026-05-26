"""Database adapter subsystem.

Provides the contract and implementations for reading metadata and
sampling values from relational databases. The classification engine
and future quality engine consume sampled data through this layer.

The adapter pattern lets dataprism support multiple databases (SQLite
in v2, PostgreSQL/MSSQL/Oracle in later versions) through one common
interface. Engine code depends on the DatabaseAdapter Protocol, not
on any specific implementation.

Public API:
    DatabaseAdapter         - Protocol any backend must satisfy
    SamplingStrategy        - SEQUENTIAL or RANDOM
    SampledValues           - rich result container with text + typed + null tracking
    TableInfo, ColumnInfo   - metadata result types
    SqliteAdapter           - concrete v2 implementation
    AdapterError            - base exception
    AdapterConnectionError  - connection failures
    AdapterQueryError       - query failures (missing tables, etc.)
"""
