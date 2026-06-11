# dataprism Architecture

This document describes the architecture of dataprism, the reasoning behind key design decisions, and what is intentionally deferred to future versions.

It is not a tutorial (see `README.md` for getting started), nor an API reference (see docstrings on individual modules). It is the place to look when you want to understand *why* something was built the way it was.

## Table of contents

1. [Overview](#1-overview)
2. [Subsystem map](#2-subsystem-map)
3. [Cross-cutting design principles](#3-cross-cutting-design-principles)
4. [Audit subsystem](#4-audit-subsystem)
5. [Policy subsystem](#5-policy-subsystem)
6. [Classification subsystem](#6-classification-subsystem)
7. [Adapters subsystem](#7-adapters-subsystem)
8. [Deferred decisions](#8-deferred-decisions)
9. [Glossary](#9-glossary)

## 1. Overview

**dataprism** is a data governance toolkit for relational data. It is a set of composable Python modules - not a framework or a service - that you wire into your own workflows. The three core subsystems (audit, policy, classification) share conventions but don't impose a fixed execution model. You decide when to load a policy, what to classify, and where to store the audit log; dataprism provides the typed building blocks.

dataprism provides three core capabilities:

- **Auditable event logging** - every governance decision is recorded in an append-only, tamper-evident log.
- **Declarative policy** - what counts as PII, what quality checks to run, what to encrypt is expressed in YAML files, not in code.
- **Pluggable enforcement engines** - classification (v1), with quality, encryption, and retention following the same pattern in later versions.

### Who it's for

The intended user is an engineer or compliance team responsible for governance over a database (or several). dataprism takes a policy file and a description of the data, and produces:

1. Classification results (which columns hold what kinds of data)
2. An audit trail (who ran what, when, against which policy, with which outcome)

### What problem it solves

The gap between "we have a data governance policy" and "we can prove we enforce it consistently" is enormous in practice. Most organizations have policies in confluence pages, spreadsheets, or somebody's head - and enforcement is ad-hoc.

dataprism's contribution is to make the policy machine-readable, the enforcement automated, and the outcome auditable. Each of those is unremarkable individually; together they let an organization answer "show me what classification ran on table X last month, and which rules matched" with a single query against the audit log.

### What dataprism is not

To set expectations precisely:

- **Not a framework**: it does not control your program's flow. You call its functions; it doesn't call yours.
- **Not a service**: there is no background process, no HTTP server, no daemon. dataprism runs inside whatever program imports it.
- **Not a database adapter**: dataprism does not connect to databases. You supply column names and sample values; database integration is a future addition.
- **Not a complete compliance solution**: it produces audit trails and classification, which are inputs to compliance, not compliance itself.

### Scope and stage

This is **v2** in progress (Phase 1 complete, Phase 2 in active development). The current scope:

**Shipped:**
- Audit subsystem (append-only, hash-chained event log)
- Policy subsystem (YAML schema + validation + audit integration)
- Classification subsystem (regex, dictionary, statistical rule evaluators)
- Adapters subsystem (`DatabaseAdapter` Protocol + `SqliteAdapter` + `PostgresAdapter`)

**In progress (v2):**
- High-level API wiring adapters + engine + audit (`classify_table(...)`)
- CLI scaffolding (`dataprism classify`, `dataprism audit verify`)
- Report generation (text + JSON output)

**Deferred (Phase 3 and beyond):**
- Quality engine pillar
- Encryption pillar
- Retention pillar
- Additional adapters (MySQL, MSSQL, Oracle)
- Multi-writer audit (Postgres-backed audit storage)

The architecture is designed so each deferred feature is additive - adding a Postgres audit backend, a CLI, or a quality engine doesn't require redesigning what exists today.

For development context: single-user, single-machine usage. No multi-writer concurrency. No async I/O. These constraints are honest about v2's deployment target, not architectural limitations.


## 2. Subsystem map

dataprism is organized into three subsystems, each in its own Python subpackage under `src/dataprism/`:

| Subsystem | Package | Purpose |
|---|---|---|
| Audit | `dataprism.audit` | Append-only, tamper-evident event log |
| Policy | `dataprism.policy` | YAML-validated governance rules |
| Classification | `dataprism.classification` | Apply policy rules to data, return matches |
| Adapters | `dataprism.adapters` | Connect to databases, sample column values |

### Dependency direction

The subsystems depend on each other in one direction only:
classification  --uses-->  policy
classification  --writes-->  audit
policy          --writes-->  audit
adapters        (depends on nothing else in dataprism)
audit           (depends on nothing else in dataprism)

In code:

```
classification.engine     imports policy.models and audit.service
classification.evaluators imports policy.models only
policy.loader             imports audit.service (via the audit-wrapping function)
policy.models             imports nothing from dataprism
audit.*                   imports nothing from dataprism (only core.exceptions)
adapters.*                imports nothing from dataprism (only core.exceptions)
```

This direction is deliberate. Audit is foundational - many subsystems write to it, but it knows nothing about them. Policy is a contract layer - classification uses it, but it doesn't reach into classification logic. Adapters are also foundational - they read from external databases but don't import any other dataprism subsystem. Classification is at the top - it consumes policy and audit; future high-level APIs (PR 8 onwards) will compose adapters with classification to produce end-to-end workflows.

There are no circular imports anywhere. If a refactor would introduce one, that's a signal something is wrong with the design.

### What each subsystem exposes

The "public" surface of each subsystem is what other subsystems and the eventual CLI depend on. Everything else is implementation detail.

**Audit (`dataprism.audit`)**
- `AuditEvent` - the immutable event record
- `EventType` - the enum of event kinds
- `AuditStorage` - the protocol any storage backend satisfies
- `AuditService` - the public write-side API
- `JsonLinesStorage`, `InMemoryStorage` - concrete storage implementations

**Policy (`dataprism.policy`)**
- `ClassificationPolicy`, `ClassificationRule` - the top-level model and discriminated union
- `RegexRule`, `DictionaryRule`, `StatisticalRule` - concrete rule models
- `ClassificationLabel`, `DictionaryMatchMode`, `RegexTarget` - supporting enums
- `load_classification_policy`, `load_and_audit_classification_policy` - the loaders
- `PolicyError`, `PolicyLoadError`, `PolicyValidationError` - the exception hierarchy

**Classification (`dataprism.classification`)**
- `ClassificationEngine` - the orchestrator
- `ClassificationResult` - the per-match result model
- `evaluate` - the singledispatch entry point (also the extension point for new rule types)

**Adapters (`dataprism.adapters`)**
- `DatabaseAdapter` - the Protocol any backend must satisfy
- `SqliteAdapter` - SQLite implementation (test backend, file-based)
- `PostgresAdapter` - PostgreSQL implementation (production target)
- `SamplingStrategy` - enum: SEQUENTIAL (default) or RANDOM
- `SampledValues` - frozen dataclass with text + typed + null tracking
- `TableInfo`, `ColumnInfo` - metadata result types
- `AdapterError`, `AdapterConnectionError`, `AdapterQueryError` - exception hierarchy

### The "audit as cross-cutting concern" insight

Notice that both policy and classification write to audit, but audit knows nothing about either. This is deliberate. Audit is *cross-cutting*: it's the substrate that records what every other subsystem does, without being coupled to any of them.

The practical consequence: when we add a quality subsystem in a future PR, it will follow the same pattern. Quality engine writes `QUALITY_CHECK_RUN` events; audit doesn't need to change. Same for encryption, retention, and anything else that comes later.

### Reading order for new contributors

If you're reading the code for the first time, this order will help:

1. `audit/events.py` - the simplest module, defines the basic event model
2. `audit/storage.py` - introduces the Protocol pattern and the hash chain
3. `audit/service.py` - the thin API on top of storage
4. `policy/models.py` - the Pydantic schemas and discriminated union
5. `policy/loader.py` - YAML to validated models, plus audit integration
6. `classification/evaluators.py` - singledispatch and pure rule evaluators
7. `classification/engine.py` - orchestration that ties everything together
8. `adapters/protocol.py` - second use of the Protocol pattern, plus the SampledValues data carrier
9. `adapters/sqlite.py` - first concrete adapter, built on SQLAlchemy Core

Each builds on the previous in concepts and dependencies. By the time you reach the classification engine, every pattern it uses has already appeared in an earlier file.


## 3. Cross-cutting design principles

Five patterns recur across dataprism's subsystems. They were chosen deliberately and applied consistently; understanding them once unlocks the rest of the code.

### Pydantic for strict data validation

Every data structure that enters dataprism from outside (YAML files, programmatic inputs) is validated by a Pydantic model with `extra="forbid"`. Unknown fields are rejected at the boundary, not silently ignored.

Why: in governance code, the cost of an undetected typo is invisible policy drift. A misspelled `classifcation: PII` (missing the second `i`) under lenient validation would silently drop the field and classify nothing. Under strict validation, the same typo fails loudly at policy load time.

The pattern: validate at the boundary, trust the model afterwards. Engine code never has to defensively check whether a field exists or has the right type - if the Pydantic model accepted the input, the contract holds.

### Dependency injection over global state

Services don't reach out for their dependencies; they receive them as constructor parameters.

```python
storage = JsonLinesStorage(path)
audit = AuditService(storage)            # storage injected
engine = ClassificationEngine(policy, audit)  # policy and audit injected
```

Why: this is what makes the test suite possible. Tests substitute `InMemoryStorage` for `JsonLinesStorage`, real Pydantic models for fake ones, real audit services for in-memory stubs. There are no module-level singletons, no global config, no `get_default_storage()` factories - just objects passed in.

The pattern applies recursively: storage is passed into the service, the service is passed into the engine, the engine is held by whatever code calls `classify()`. The composition root (the place where dependencies are wired) lives in the user's code, not inside dataprism.

### Append-only audit as the substrate

Every meaningful action in dataprism produces an audit event. Policy loads, classification runs, validation failures - all of them write to the audit log before returning.

Why: governance is about being able to answer "what happened?" months later. A system that only logs failures misses half the story. A system that logs everything but loses tamper-evidence misses the other half. dataprism's audit log records every governance decision and chains records cryptographically.

The pattern: every subsystem that does meaningful work takes an `AuditService` in its constructor. The cross-cutting nature of audit is what justifies the inverted dependency direction (audit knows nothing about its callers).

### Strategy pattern at extension points

When a subsystem has multiple possible implementations of one role, dataprism uses a structural-typing protocol (or singledispatch) rather than inheritance.

Two examples:

- `AuditStorage` is a `typing.Protocol`. `InMemoryStorage` and `JsonLinesStorage` satisfy it without inheriting from it. A future `PostgresStorage` would do the same.
- `evaluate` in classification uses `functools.singledispatch`. Three implementations registered for three rule types. A future `IPRangeRule` would add a new registered function without touching existing code.

Why: protocols and singledispatch are Python's mechanisms for "many implementations, one interface, no inheritance gymnastics." They keep extension points open without imposing a base-class hierarchy that you'd have to plan in advance.

The pattern: if you find yourself writing `class MyImpl(BaseFoo):` and the only reason is to satisfy a type contract, use a Protocol instead. If you find yourself writing an isinstance chain to dispatch on type, use singledispatch.

#### Why Protocol instead of inheritance, in more depth

The choice of `typing.Protocol` over abstract base classes was deliberate. The trade-offs:

**What we gain with Protocol (structural subtyping):**

- **No fake parent in the class hierarchy.** `SqliteAdapter.__bases__` is `(object,)`, not `(DatabaseAdapter,)`. The class stands alone; its parent isn't an implementation detail of how dataprism wires together extension points.
- **No multiple-inheritance gymnastics.** A class can satisfy multiple Protocols just by having the right methods - no diamond problem, no MRO surprises, no thinking about which parent's `__init__` gets called.
- **Third-party classes work for free.** If an external library has a class that happens to match our `DatabaseAdapter` contract, it can be passed to dataprism without modification or a wrapper class. The contract is structural, not nominal.
- **Tests substitute fakes effortlessly.** A `FakeAdapter` with the right method signatures works without inheriting from anything. No mocking library needed; no abstract base class to satisfy.
- **Refactoring is cheaper.** Extracting a broader Protocol (say, `ReadableTabularSource` covering adapters, spreadsheets, and HTTP endpoints) doesn't require changing every existing class's inheritance declaration. Existing classes automatically satisfy the new Protocol if their methods match.

**What inheritance would offer instead:**

- **Implementation sharing via `super().method()`.** Subclasses can reuse parent code. We don't need this - adapter implementations diverge significantly between databases.
- **Explicit "X is a Y" declaration at the class header.** Modern type checkers find the relationship through Protocol anyway; explicit declaration adds little value.
- **Built-in `isinstance(obj, BaseClass)` checks.** With Protocol, `isinstance()` works only when the Protocol is decorated `@runtime_checkable`. We deliberately don't use `@runtime_checkable` because we never check types at runtime - we just call methods.

**The mental model:**

Python supports both nominal subtyping (inheritance: "X is a Y because I declared X to be a kind of Y") and structural subtyping (Protocol: "X is a Y because X has the methods that Y requires"). The two patterns coexist:

- For "many implementations, one interface" - exactly what `DatabaseAdapter`, `AuditStorage`, and future Protocols solve - Protocol is the right tool.
- For "share implementation across related classes" - which dataprism doesn't need - inheritance is the right tool.

Where in the code: `AuditStorage` Protocol in `audit/storage.py`, `DatabaseAdapter` Protocol in `adapters/protocol.py`. Both follow the same pattern; both intentionally lack `@runtime_checkable`.

### YAGNI as the default for v1

When a decision could go either way, the smaller and more reversible option wins. Examples from across the codebase:

- One storage file for both `InMemoryStorage` and `JsonLinesStorage` instead of splitting into separate files. Refactoring later is 15 minutes; refactoring now would be premature.
- No plugin system. Could add one later if needed; would have been complexity overhead today.
- No precedence resolution between matching rules. All matches returned; caller decides. Resolution can be layered on top later.
- No multi-version policy support. Schema version field exists; multi-version loading would be added when v2 of a schema appears.
- No compiled regex caching in evaluators. Premature optimization without measurement.

Why: small projects pay a real cost for premature complexity. Each "future-proofing" decision adds machinery that has to be maintained, tested, and understood. The cost of refactoring later is almost always lower than the cost of carrying complexity forward.

The pattern: when in doubt, choose the simpler option and document the trigger for revisiting. "We'd add precedence resolution if a real user reported a conflict they couldn't handle" is more useful than building precedence resolution now in case someone might need it.


## 4. Audit subsystem

Package: `dataprism.audit`

### Purpose

Record every governance action in a durable, tamper-evident log so that compliance reviewers months later can answer "what happened, when, and was the record altered?" with confidence.

The audit subsystem is the foundation. Every other subsystem writes to it. Anything that matters for compliance lives here.

### Key design decisions

**Append-only, never edited**

Audit records are never updated or deleted after they're written. The log grows; entries don't change. This is the prerequisite for any meaningful tamper detection - if entries could be legitimately modified, "modification" would have no signal.

The `AuditEvent` model is `frozen=True` to enforce this in code: trying to set an attribute after construction raises `ValidationError`. Pydantic backs up the policy in the type system.

**Hash-chained for tamper-evidence**

Each persisted record includes the SHA-256 hash of the previous record. Tampering with any past record invalidates its hash, which breaks the chain at the *next* record, which breaks at the next, and so on - all the way to the end of the log.

`JsonLinesStorage.verify()` walks the chain. If any link is broken, it raises `ChainVerificationError` with the exact position. The chain doesn't prevent tampering - it makes silent tampering practically detectable, which is the realistic threat model.

This is not blockchain. There's no consensus, no proof of work, no distributed ledger. It's a much simpler property: "if you change history, the chain breaks and the change is visible." Same property as git history, used here for the same reason.

**Strategy pattern for storage**

`AuditStorage` is a `typing.Protocol` describing the contract any backend must satisfy: `append()`, `read_all()`, `verify()`. Two implementations ship in v1:

- `InMemoryStorage` - list-backed, for tests and demos. No persistence, no chaining.
- `JsonLinesStorage` - file-backed, with the hash chain.

A future `PostgresStorage` would satisfy the same protocol with no changes to the rest of the codebase. The dependency direction (engine code depends on the protocol, not on a specific implementation) is what makes this swap trivial.

**Single-writer assumption**

`JsonLinesStorage` does not synchronize concurrent writes. Two processes appending to the same file simultaneously would corrupt the chain. For v1 (single-process, single-machine) this is acceptable and explicitly documented. Multi-writer support means moving to a database backend; we'd add `PostgresStorage` when that becomes necessary.

### Public API

```python
from dataprism.audit.events import AuditEvent, EventType
from dataprism.audit.service import AuditService
from dataprism.audit.storage import (
    AuditStorage,
    InMemoryStorage,
    JsonLinesStorage,
    ChainVerificationError,
)

storage = JsonLinesStorage(Path("audit.jsonl"))
service = AuditService(storage)
service.record(
    event_type=EventType.CLASSIFICATION_RUN,
    actor="cli",
    data={"column_name": "email", "matches": 1},
)

# Later, verify the chain:
storage.verify()  # raises ChainVerificationError if tampered
```

### Internals worth understanding

**Genesis hash**

The first record in a chain references a fixed "genesis" hash (64 zero characters). This is just a placeholder meaning "start of chain." It has no cryptographic significance - any agreed-upon constant works. Using zeros is the convention.

**Deterministic serialization**

The hash is computed over a JSON serialization of the record content. For the chain to verify, that serialization must be byte-for-byte identical on every machine. We use `json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)` to guarantee this - sorted keys, no whitespace variation, deterministic conversion for non-JSON-native types like UUIDs and datetimes.

If you ever change the hashing function or serialization format, you've broken backward compatibility with all existing audit logs. This is a real cost worth being aware of before tweaking.

**Storage and event are decoupled**

`AuditEvent` itself doesn't have a `prev_hash` or `hash` field. Those exist only on the persisted record (the JSON written to disk). Storage adds them on write and strips them on read. This keeps the in-memory event model clean and storage concerns where they belong.

### Limitations

Documented constraints worth knowing:

- **Single-writer**: concurrent appends from multiple processes will corrupt the chain. Use file locking or move to a DB if you need multi-writer.
- **No log rotation**: the JSON Lines file grows indefinitely. For long-running deployments, you'd add rotation (with a fresh genesis hash per file segment, or with chain continuation logic).
- **Whole-file verification**: `verify()` reads the entire log. For million-record logs, that's still seconds, but at hundreds of millions you'd want incremental verification.
- **Tampering with the entire file is not detected**: if someone replaces the file with an empty valid chain, that chain verifies in isolation. The defense against this is external checkpointing (periodically writing the current chain head hash to a separate trusted location), which v1 doesn't implement.
- **No encryption at rest**: the audit log is plaintext JSON. If the log itself contains sensitive data, the underlying filesystem must be encrypted separately.


## 5. Policy subsystem

Package: `dataprism.policy`

### Purpose

Provide a declarative language for governance rules. Policy files (YAML) describe *what* should be governed (which patterns are PII, what's classified as financial, etc.); engine code interprets them. Without a policy layer, governance rules live in code, and changing them requires code changes and deployments.

The policy subsystem does three things:

1. Defines the schema for valid policy files (Pydantic models)
2. Loads YAML files and validates them against the schema (loader)
3. Records audit events on load success and failure (audit integration)

### Key design decisions

**YAML, not Python or JSON**

Policy files are YAML because the audience is broader than engineers. Compliance officers, data stewards, and security engineers should be able to edit policies without learning Python. YAML supports comments (JSON doesn't), is forgiving with quoting, and handles nested structures cleanly. The trade-off (indentation sensitivity, occasional parse surprises) is acceptable for the audience.

**Strict Pydantic validation**

Every policy model uses `model_config = ConfigDict(extra="forbid")`. A YAML file with an unknown field is rejected, not silently accepted. A typo in `classification` (missing a letter) becomes a load error, not silent policy drift.

This is non-negotiable for governance code. Lenient parsing means a policy that *looks* enforced might not be, and you'd discover this six months later when a regulator asks why.

**Discriminated union for rule types**

Three rule types share a parent name (`ClassificationRule`) but have distinct shapes. Pydantic's discriminated union pattern routes validation by the `type` field:

```python
ClassificationRule = Annotated[
    RegexRule | DictionaryRule | StatisticalRule,
    Field(discriminator="type"),
]
```

A YAML rule with `type: regex` must satisfy `RegexRule` (with `target`, `pattern`, etc.); a rule with `type: dictionary` must satisfy `DictionaryRule` (with `values`, `match_mode`, etc.). Mixing fields between types is caught at load time.

Adding a new rule type later is a non-breaking change: define a new Pydantic model, add it to the union. Existing policy files continue loading; new rule types become available for new policies.

**Pure loader + audit-wrapping function (Option C from the design discussion)**

The loader is split into two functions:

- `load_classification_policy(path)` - pure, no side effects. Reads YAML, validates, raises specific errors. Trivially testable.
- `load_and_audit_classification_policy(path, audit_service)` - wraps the pure loader with audit event recording (`POLICY_LOADED` on success, `POLICY_VALIDATION_FAILED` on failure).

The pure loader has no dependency on audit. The audit-wrapping function adds the cross-cutting concern. Most production callers will use the audit-wrapped version; tests usually use the pure version.

**Three-level exception hierarchy**

Policy failures fall into two distinct categories with different appropriate responses:

- `PolicyLoadError` - file can't be read or parsed as YAML (transient, possibly retryable)
- `PolicyValidationError` - YAML parses but doesn't match schema (permanent, fix the file)
- `PolicyError` - common base, useful for catch-all handling

Splitting the failure modes lets callers handle them differently. A scheduled job might retry a `PolicyLoadError` (the file might be mid-write) but not a `PolicyValidationError` (the file is wrong; retrying won't help).

**Six classification labels, not extensible per-rule**

The `ClassificationLabel` enum has six members: PII, PHI, FINANCIAL, CREDENTIAL, PUBLIC, INTERNAL. These cover the common cases for most policies. Adding a label is non-breaking; removing one is breaking.

The decision not to allow custom labels per policy was deliberate: a fixed vocabulary makes audit logs comparable across policies and across time. If a policy could declare "MY_CUSTOM_LABEL", comparing classifications between two deployments becomes guesswork. Future versions could extend the enum, but per-policy custom labels are explicitly not on the roadmap.

**Config vs policy distinction**

Policy files (YAML governance rules) and configuration (environment paths, credentials) are separate concerns. Policy travels between dev/staging/prod unchanged; configuration differs per environment. dataprism v1 only has policy files; configuration via `pydantic-settings` would be added once dataprism has runtime needs (e.g., storage paths via env vars).

This separation is structural even before it's mechanically needed - the policy directory (`config/policies/`) is separate from any future settings file, and no policy model contains paths or credentials.

### Public API

```python
from pathlib import Path

from dataprism.audit.service import AuditService
from dataprism.audit.storage import JsonLinesStorage
from dataprism.policy.errors import (
    PolicyError,
    PolicyLoadError,
    PolicyValidationError,
)
from dataprism.policy.loader import (
    load_classification_policy,
    load_and_audit_classification_policy,
)
from dataprism.policy.models import (
    ClassificationLabel,
    ClassificationPolicy,
    ClassificationRule,
    DictionaryMatchMode,
    DictionaryRule,
    RegexRule,
    RegexTarget,
    StatisticalRule,
)

# Pure load (raises on any failure)
policy = load_classification_policy(Path("policy.yaml"))

# Audit-wrapped load (records success/failure events)
audit = AuditService(JsonLinesStorage(Path("audit.jsonl")))
policy = load_and_audit_classification_policy(
    Path("policy.yaml"),
    audit,
    actor="cli-user",
)
```

### Internals worth understanding

**Two-stage error handling in the loader**

The loader has two distinct stages with different error types:

1. **Read and parse YAML** - failures here are `PolicyLoadError` (file not found, permission denied, invalid YAML syntax)
2. **Validate against schema** - failures here are `PolicyValidationError` (missing required field, unknown field, wrong type, bad enum value)

Both wrap the original exception via `raise ... from e` so the underlying cause is preserved as `__cause__`. Debug output can chase the chain back to the real problem.

**Empty file handling**

`yaml.safe_load` returns `None` for an empty file. Without an explicit check, `ClassificationPolicy.model_validate(None)` would fail with a confusing Pydantic error message. The loader converts this case to a clear `PolicyLoadError("Policy file ... is empty")` upfront.

**`yaml.safe_load`, not `yaml.load`**

`yaml.load` can execute arbitrary Python code through tags like `!!python/object`. That's a security disaster for any code that loads user-supplied YAML. dataprism only ever uses `yaml.safe_load`, which constructs basic Python types only.

**Defaults applied during validation**

`DictionaryRule.match_mode` defaults to `EXACT_NORMALIZED`. `StatisticalRule.sample_size` defaults to 1000. `StatisticalRule.min_match_ratio` defaults to 0.95. These defaults are applied automatically when a YAML file omits the field. Tests verify this explicitly (`test_minimal_fixture_applies_default_match_mode`).

### Limitations

- **No multi-version policy support**: the schema has a `version` field, but loading currently accepts only v1 schemas. Future schema versions would need explicit migration logic in the loader.
- **No policy composition/inheritance**: a policy file can't `extends:` another policy. If you need to share rules across policies, duplicate them. Composition could be added but adds significant complexity.
- **No regulatory framework field**: regulatory mapping (PDPA, GDPR, etc.) is documented in the example file's comments, not encoded in the schema. A future `applies_to_frameworks: list[str]` field per rule would formalize this, at the cost of more YAML.
- **Labels are enum members**: adding a label is a code change, not just a YAML change. This is intentional (fixed vocabulary supports comparable audit logs) but does mean operators can't define org-specific labels without modifying dataprism.


## 6. Classification subsystem

Package: `dataprism.classification`

### Purpose

Apply policy rules to columns and return matches. Given a loaded `ClassificationPolicy` and a column (its name plus optional sample values), the engine evaluates every rule and returns one `ClassificationResult` per match. Every call also records a `CLASSIFICATION_RUN` audit event.

This is the first subsystem that *does* something with data, as opposed to defining or storing rules. Audit is the substrate and policy is the language; classification is the first real application.

### Key design decisions

**Singledispatch for rule evaluation (Option B from the design discussion)**

The `evaluate()` function in `evaluators.py` uses `functools.singledispatch`. Three registered implementations handle the three rule types:

```python
@singledispatch
def evaluate(rule, column_name, values): ...

@evaluate.register
def _(rule: RegexRule, ...): ...

@evaluate.register
def _(rule: DictionaryRule, ...): ...

@evaluate.register
def _(rule: StatisticalRule, ...): ...
```

At runtime, calling `evaluate(rule, ...)` dispatches to the implementation registered for `type(rule)`. The dispatch table is a simple Python dict, queryable via `evaluate.registry` and `evaluate.dispatch(SomeRuleType)`.

The alternative considered was a `ClassificationEngine` class with an `isinstance` chain. Both work; singledispatch was chosen for three reasons:

1. **Pure functions are easier to test.** Each evaluator is a standalone function callable directly without instantiating an engine.
2. **Extensibility is structural.** Adding `IPRangeRule` means writing one new `@evaluate.register` function. The engine is unchanged; no `isinstance` chain to maintain.
3. **The dispatch table is honest.** `evaluate.registry` shows exactly which types have registered implementations - more discoverable than reading an if/elif chain.

**Engine as orchestrator, not evaluator**

`ClassificationEngine` doesn't contain rule-specific logic. It iterates the policy's rules, calls `evaluate()` for each, collects matches, and records audit. All rule-type-specific behavior lives in `evaluators.py`.

This separation matters for the same reason audit lives separately from its callers: the engine should be replaceable. A future "parallel classification engine" or "streaming classification engine" would reuse the same evaluators with different orchestration.

**All matches returned, not first-match or highest-precedence (Option B from the design discussion)**

When a column matches three rules, the engine returns three `ClassificationResult` instances. The caller decides what to do with overlapping classifications.

The two alternatives:
- *First-match wins* would lose information; later rules might be more specific and correct.
- *Highest-precedence label wins* would require defining an ordering between labels (PHI > FINANCIAL > PII > INTERNAL > PUBLIC?), which encodes a policy decision in code.

Returning all matches keeps the engine policy-free. Precedence resolution can be layered on top later by callers or by a future "policy merger" subsystem.

**One audit event per `classify()` call, not per match**

Whether a column matches zero rules or ten, the engine records exactly one `CLASSIFICATION_RUN` event. The event's `data` includes:

- `column_name` - which column was evaluated
- `rules_evaluated` - how many rules ran
- `matches` - how many matched (zero or more)
- `matched_rules` - the names of matching rules

Why one event, not per-match: compliance reviewers care about "did dataprism look at this column?" as much as "what matched?" A column with zero matches is meaningful audit data - the engine ran but nothing flagged. Recording silence is what makes the audit log a complete record of governance activity.

**Empty values mean "no evidence", not "vacuously true"**

A regex rule with `target=COLUMN_VALUE` and no values to evaluate against returns `False`. Python's `all([])` returns `True` (vacuous truth), but applying that logic here would mean "the rule matches" when no values exist to evaluate. We treat that as a non-match.

Similarly, statistical rules with empty values return `False`. Match ratio over zero samples is undefined; we don't claim a match without evidence.

This is the conservative choice. A "we don't know" outcome is closer to "no" than "yes" for governance.

**`ClassificationResult.classification` is a plain string**

The result model stores classification as `str`, not `ClassificationLabel`. This decouples `results.py` from `policy.models` - the result subsystem doesn't need to import the policy enum.

The trade-off: callers wanting type-safe comparisons (`if result.classification == ClassificationLabel.PII:`) would need to compare against the enum's `.value` (`"PII"`) instead. Acceptable for v1; could revisit if a real call site finds this awkward.

### Public API

```python
from pathlib import Path

from dataprism.audit.service import AuditService
from dataprism.audit.storage import JsonLinesStorage
from dataprism.classification.engine import ClassificationEngine
from dataprism.classification.results import ClassificationResult
from dataprism.policy.loader import load_classification_policy

policy = load_classification_policy(Path("policy.yaml"))
audit = AuditService(JsonLinesStorage(Path("audit.jsonl")))
engine = ClassificationEngine(policy, audit, actor="cli-user")

results = engine.classify(
    column_name="email_address",
    values=["alice@example.com", "bob@example.com"],
)
# results: list[ClassificationResult]
# One entry per matched rule, empty list if nothing matched.

# For extension - register a new rule type's evaluator:
from dataprism.classification.evaluators import evaluate

@evaluate.register
def _(rule: MyCustomRule, column_name, values):
    ...
```

### Internals worth understanding

**The three evaluators are pure functions**

Each `@evaluate.register` function takes a rule, a column name, and values; returns `bool`. No state, no side effects, no I/O. This is what makes them so testable - construct a rule, call `evaluate(rule, ...)`, assert on the result.

**The `_normalize()` helper**

Used by the dictionary evaluator for `EXACT_NORMALIZED` and `CONTAINS_NORMALIZED` modes. Lowercases the input and strips three characters: underscore, hyphen, space. So `Email_Address`, `email-address`, and `Email Address` all normalize to `emailaddress`.

This is the smallest possible normalization that handles the common naming variations seen in real databases. More aggressive normalization (stemming, soundex, embeddings) is out of scope for v1.

**Dictionary match modes**

Three modes with progressively looser matching:

- `EXACT` - byte-for-byte equality, case-sensitive
- `EXACT_NORMALIZED` (default) - equality after normalization
- `CONTAINS_NORMALIZED` - substring match after normalization

The choice between modes is a trade-off between false positives and false negatives:

- `EXACT` has zero false positives but misses every case/separator variant
- `EXACT_NORMALIZED` catches naming variations but doesn't catch prefix/suffix variants like `customer_email`
- `CONTAINS_NORMALIZED` catches the most variants but has documented false positives (`email` matches `emailable`)

Real-world policies combine modes: `EXACT_NORMALIZED` for the common case, `CONTAINS_NORMALIZED` for high-signal keywords only (SSN, passport - words rarely embedded in unrelated columns).

**Regex targets**

`RegexRule` has a required `target` field with two values:

- `COLUMN_NAME` - the pattern is matched against the column's name
- `COLUMN_VALUE` - the pattern must match every sampled value (all-or-nothing)

For probabilistic value matching (some-but-not-all values match), use `StatisticalRule` instead. The explicit split prevents confusion about "what does regex on values actually mean."

**Statistical sampling**

`StatisticalRule.sample_size` limits how many values are evaluated. If a column has 1,000,000 rows but `sample_size=1000`, only the first 1000 values are checked. The match ratio is computed over the sample, not the full column.

This is the right default for v1: random sampling has cost; "first N values" is fast and usually good enough for classification. For statistical rigor (random sampling, confidence intervals), the engine would need a separate sampling layer that v1 doesn't have.

### Limitations

- **No precedence between matching rules**: all matches are returned. If a column matches both a PII rule and a FINANCIAL rule, both appear in results. The caller resolves conflicts.
- **No incremental classification**: each `classify()` call evaluates all rules. There's no caching of "this rule already matched this column last time."
- **No batch interface**: classifying many columns means many `classify()` calls. A future `classify_many()` could batch audit events and amortize policy iteration; v1 keeps the simple per-column API.
- **Statistical sampling is sequential, not random**: `sample_size` takes the first N values. For statistically rigorous sampling, the caller must shuffle first.
- **Regex patterns are not pre-compiled**: each evaluator call compiles its pattern fresh. For policies with many statistical rules evaluated against many columns, this is measurable overhead. Caching could be added if measurement shows it matters.
- **No database awareness**: the engine doesn't know about column data types, nullability, primary keys, etc. It treats everything as strings. A future engine version could honor type metadata.


## 7. Adapters subsystem

Package: `dataprism.adapters`

### Purpose

Connect to real databases, read their structure, and sample column values. The classification engine (and future quality engine) consume data through this layer rather than asking callers to supply it directly.

Before v2, the engine took pre-supplied column data: callers had to extract values from their database themselves and pass them in as `list[str]`. That made dataprism a Python library but not a tool. The adapter subsystem closes that gap - point dataprism at a database, get classification results without writing your own data-extraction code.

The v2 deliverable is SQLite. Future versions add PostgreSQL, MySQL, MSSQL, Oracle, etc. - each as a new adapter class that satisfies the same Protocol.

### Key design decisions

**SQLAlchemy Core, not the ORM**

dataprism uses SQLAlchemy at the Core level, not the ORM. The distinction matters:

- ORM (Object-Relational Mapper): maps Python classes to tables. Useful when your application owns the schema and persists its own data.
- Core: SQL expression language. Lower-level, closer to raw SQL.

dataprism doesn't own the schema of the databases it inspects - we're reading metadata and sampling values from arbitrary tables, not persisting our own data. Core gives the right level of abstraction: dialect-agnostic SQL construction without the overhead of class-to-table mapping.

The practical consequence: the same Python code works against SQLite, PostgreSQL, MSSQL, MySQL, and Oracle. SQLAlchemy translates dialect-specific differences (function names, type representations, schema concepts) under the hood. Adding a new database typically means a new adapter class with a different connection-string format, not a rewrite of the SQL.

**Protocol-based extension (same Strategy pattern as audit storage)**

`DatabaseAdapter` is a `typing.Protocol`. Two concrete adapters satisfy it structurally in v2: `SqliteAdapter` and `PostgresAdapter`. Neither inherits from a base class - they just match the contract. Future adapters (MySQL, MSSQL, Oracle) follow the same pattern.

The two v2 adapters serve complementary roles:
- `SqliteAdapter` is the **test backend**. Fast (in-memory or temp-file SQLite), no infrastructure, exercised by 39 contract tests.
- `PostgresAdapter` is the **production target**. Tested against real Postgres for Postgres-specific behaviors (schemas, BOOLEAN type, network failures). Validates the abstraction holds against a real-world database.

This mirrors the pattern from the audit subsystem (`AuditStorage` Protocol, `JsonLinesStorage` and `InMemoryStorage` as implementations). The same architectural decision applied to a different domain. New backends are additive; no existing code changes when a new one arrives.

**Path coercion at the adapter boundary**

`connect()` accepts both `str` and `Path`:

```python
adapter.connect("sqlite:///path/to/db.sqlite")    # DSN string
adapter.connect(Path("data.sqlite"))              # pathlib.Path
```

The `_normalize_dsn()` helper converts a `Path` to a DSN string. This insulates callers from one of the more annoying differences between databases - file-based databases (SQLite) use file paths, network databases (Postgres) use connection strings. The adapter accepts whichever is appropriate.

This came out of the v2 scoping discussion: rather than forcing CLI code (and future API code) to construct DSNs by hand, we normalize at the protocol boundary.

**SampledValues container (the Option D refinement)**

`sample_values()` returns a `SampledValues` dataclass rather than a plain `list[str]`. The dataclass carries multiple representations:

| Field | What it holds | Used by |
|---|---|---|
| `text` | Stringified values, NULLs filtered out | Classification (current) |
| `typed` | Native Python types, NULLs preserved as None | Quality engine (future) |
| `null_count` | Count of NULL values in the sample | Reports, quality checks |
| `sample_size_requested` | The `n` parameter that was passed | Audit, reports |
| `sample_size_actual` | Count of rows actually returned (may be less than `n`) | Audit, reports |

Why both `text` and `typed`: classification rules pattern-match on strings; quality rules need numeric or temporal types for min/max/distribution operations. By having the adapter produce both representations in one query, we avoid round-trips and ensure the v3 quality engine has what it needs without re-fetching.

The cost is small (one Python list instead of one) and avoided expensive future refactoring of every adapter when the quality engine arrives.

**SEQUENTIAL is the default sampling strategy**

Two strategies are available:

- `SamplingStrategy.SEQUENTIAL` (default): the first `n` values in storage order. Fast (`LIMIT n`), deterministic.
- `SamplingStrategy.RANDOM`: a random sample of `n` values. Slower (forces a full scan), statistically representative.

SEQUENTIAL is the default because classification is robust to ordering - we're asking "does this column look like PII?", which doesn't depend on which N rows we look at as long as we look at enough of them. Deterministic sampling also means two classification runs against the same data produce the same audit trail, which matters for compliance reproducibility.

RANDOM is available for callers that specifically want sample-level rigor. Future quality work (outlier detection, distribution analysis) is more likely to use it.

**Timezone normalization in `_to_str()`**

When converting datetime values to strings for the `text` field, timezone-aware datetimes are normalized to UTC before stringifying. Naive datetimes (no timezone info) are stringified as-is.

This prevents subtle bugs where the same logical timestamp appears as two different strings depending on which timezone the database driver chose to report. UTC normalization gives a canonical form.

The trade-off: naive datetimes can't be normalized (we don't know what timezone they're in), so they pass through unchanged. Database integrations should prefer storing timestamps with timezone info; dataprism documents the constraint rather than guessing.

**NULL handling: filter in Python, not SQL**

The earlier draft of `sample_values()` filtered NULLs at the SQL level (`WHERE column IS NOT NULL`). We changed this to filter in Python after fetching.

Why: the SampledValues contract requires `null_count` - we need to know how many NULLs the column had in the sample. Filtering at SQL level loses that information. Fetching all rows including NULLs, then filtering for `text` while preserving `typed`, lets us populate both fields and `null_count` correctly.

The cost: a column that's 99% NULL with `n=1000` requested fetches 1000 mostly-NULL rows. For pathological cases this is wasteful, but the rare and predictable nature of "almost-all-NULL columns" makes optimization premature. A future "skip NULLs at SQL level" mode could be added if real workloads need it.

### Public API

```python
from pathlib import Path

from dataprism.adapters.errors import (
    AdapterConnectionError,
    AdapterError,
    AdapterQueryError,
)
from dataprism.adapters.protocol import (
    ColumnInfo,
    DatabaseAdapter,
    SampledValues,
    SamplingStrategy,
    TableInfo,
)
from dataprism.adapters.sqlite import SqliteAdapter

# Construct (cheap, no I/O)
adapter = SqliteAdapter()

# Open connection (may fail)
adapter.connect(Path("mydata.sqlite"))    # accepts Path or str DSN

# Use
try:
    tables = adapter.list_tables()           # list[TableInfo]
    columns = adapter.list_columns("users")  # list[ColumnInfo]
    samples = adapter.sample_values(
        "users", "email",
        n=1000,
        strategy=SamplingStrategy.SEQUENTIAL,
    )
    # samples.text   - list[str], NULLs filtered, ready for classification
    # samples.typed  - list[Any], NULLs preserved as None
    # samples.null_count, samples.sample_size_actual - metadata
finally:
    adapter.close()
```

### Internals worth understanding

**`_require_connected()` pattern**

Every public method (other than `connect()` and `close()`) calls `_require_connected()` first. This raises `AdapterError` if the adapter hasn't been connected yet. Without this guard, operations on a not-connected adapter would fail with confusing `AttributeError` ("NoneType has no attribute X") from SQLAlchemy internals.

The pattern is a single line per method, but it's the difference between a clear "Adapter not connected. Call connect() first." and a stack trace pointing at SQLAlchemy code that means nothing to the caller.

**SQLAlchemy is lazy at connection time**

`create_engine(dsn)` returns immediately, even with a bad DSN. SQLAlchemy doesn't actually open a connection until a query runs.

This is good for performance but bad for catching connection errors at the right time. The adapter's `connect()` method forces a connection via `with self._engine.connect(): pass` to verify the DSN works upfront. Without this, callers would only learn about authentication failures or missing files when they later called `list_tables()`, which is misleading.

**SQLAlchemy logging configuration**

`sqlite.py` has this at module load time:

```python
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
```

Why: SQLAlchemy's default log level for the engine is INFO, which dumps every SQL query it executes. In tests this produces hundreds of lines of SQL spam that drowns out actual test output. In production it could log sensitive data.

The configuration is module-level so it applies the moment anyone imports the adapter package. No call site has to remember to silence the logger.

**Why we use `func.random()`, not `random()` or `RAND()`**

The RANDOM sampling strategy uses SQLAlchemy's `func.random()` instead of writing raw SQL. SQLAlchemy translates this to the appropriate function for the dialect:

- SQLite: `random()`
- PostgreSQL: `random()`
- MSSQL: `NEWID()` (yes, very different)
- Oracle: `DBMS_RANDOM.VALUE`
- MySQL: `RAND()`

Hand-writing `ORDER BY random()` would work on SQLite and Postgres but fail on MSSQL, Oracle, and MySQL. The Core-level `func.random()` is the dialect-agnostic form that just works.

### Limitations

- **SQLite and PostgreSQL only in v2**: MSSQL, Oracle, MySQL adapters are deferred. Each would be ~150 lines following the same pattern; the Protocol is unchanged.
- **No connection pooling**: each `SqliteAdapter` instance owns one engine. For high-throughput multi-connection scenarios, you'd want connection pooling, which SQLAlchemy supports but our adapter doesn't expose.
- **No async**: all adapter methods are synchronous. SQLAlchemy 2.0 supports async, but adding it would be a parallel API rather than a drop-in upgrade. v2's sync scope is sufficient for CLI use; future API/service use might motivate async.
- **No transactions across calls**: each call opens its own connection via `with self._engine.connect(): ...`. There's no notion of "do these three things in one transaction." For our read-only inspection workload, this is fine; future write-capable adapters would need transactional semantics.
- **No schema awareness for SQLite**: the `schema` parameter on `list_tables()` is accepted (for protocol consistency) but ignored by `SqliteAdapter`. SQLite doesn't have schemas in the Postgres sense. `PostgresAdapter` honors the parameter; future MySQL/MSSQL/Oracle adapters will too.
- **Sample size strict cap**: `sample_values(n=1000)` truncates at 1000 rows regardless of NULL density. If 999 of those 1000 are NULL, you get one `text` value. The contract is "fetch at most n rows from the database," not "return at most n non-NULL values."
- **No row counts**: there's no `count_rows(table)` method. Counting rows on large tables is expensive (potentially a full scan); we don't include it until something actually needs it.
- **No incremental sampling**: each call to `sample_values()` is independent. There's no "give me the next 1000 rows that I haven't seen yet" mode.
- **No partition or filter support**: you can't say "sample from this column where region='APAC'". Filtering would require either threading filter clauses through the API (significant) or letting callers construct their own SQL.
- **SQLAlchemy 2.0 dependency**: SQLAlchemy is now a hard dependency. We use Core only (no ORM), but the package size and load time aren't trivial. For environments that need a smaller footprint, this is a real cost.


## 8. Deferred decisions

dataprism v1 is intentionally small. Many things one might expect from a "complete" governance tool are explicitly not in v1. This section enumerates them and explains the reasoning, so future contributors (including future-you) can decide when each becomes worth doing.

The structure is consistent: what was deferred, why, what triggers revisiting.

### Quality engine

- **What**: A subsystem analogous to classification that runs quality checks (null rates, value distributions, referential integrity, etc.) against columns based on policy.
- **Why deferred**: Phase 1 focuses on classification because it's the simpler pillar - rules return booleans. Quality rules need richer outputs (statistics, thresholds, severity levels). Building both at once would double the surface area to verify.
- **Trigger to revisit**: Phase 2 of the project. Quality follows the same architectural pattern as classification (policy + engine + evaluators + audit), so the precedent is set.

### Encryption engine

- **What**: A subsystem that uses policy to decide which columns to encrypt at rest, with key management integration.
- **Why deferred**: Encryption requires production-grade key management (KMS integration, key rotation, audit of key usage). That's a significant subsystem of its own, not a small addition to dataprism. Doing it half-correctly is worse than not doing it.
- **Trigger to revisit**: When dataprism has database adapters and a real production deployment with KMS available.

### Retention engine

- **What**: A subsystem that enforces data lifecycle policies (delete records older than N days, archive after M years).
- **Why deferred**: Retention requires database write access and reliable scheduling. Both are out of scope for v1, which is read-only and stateless.
- **Trigger to revisit**: When dataprism has database adapters and a scheduler (or runs inside one).

### Additional database adapters

- **What**: MySQL, MSSQL, Oracle adapter implementations. v2 ships `SqliteAdapter` (test backend) and `PostgresAdapter` (production target). The remaining backends are deferred.
- **Why deferred**: Each adapter is ~150 lines following the established pattern, but each adds its own integration testing burden (test database, dialect-specific edge cases). The Protocol is validated against two backends now (SQLite + PostgreSQL); adding more is on demand.
- **Trigger to revisit**: When a real-world workload needs one of the deferred backends. Adding an adapter is non-breaking - it's a new class satisfying the existing Protocol.

### CLI

- **What**: A `dataprism` command-line tool: `dataprism classify --policy policies/x.yaml --table mydb.users`.
- **Why deferred**: A CLI is a thin layer over the programmatic API. Until the API surface stabilizes, a CLI would have to change every time something underneath changes. Building it after the programmatic API is settled is more efficient.
- **Trigger to revisit**: Once Phase 2 lands database adapters. Then "dataprism classify against a real database" becomes a useful CLI command.

### Multi-writer audit

- **What**: An audit storage backend that handles concurrent writes from multiple processes safely.
- **Why deferred**: Concurrency requires either file-level locking (slow, error-prone on Windows) or a database backend (postgres, sqlite). For single-machine, single-user v1, neither is necessary. Documenting the single-writer assumption is honest; building concurrency for unused scenarios is premature.
- **Trigger to revisit**: When dataprism is deployed in a multi-process context (e.g., as a service called by multiple workers). Solution: write `PostgresStorage` as a new `AuditStorage` implementation.

### Audit log rotation and archival

- **What**: Automatic log rotation when the JSON Lines file exceeds a size threshold, with chain continuation across files (the new file's genesis hash is the previous file's tail hash).
- **Why deferred**: Real-world log rotation requires careful design (where to write, how to compress, retention of historical logs). It's straightforward but not free, and v1 deployments are unlikely to hit log sizes that matter.
- **Trigger to revisit**: When a v1 deployment reports a log file in the tens or hundreds of megabytes that's becoming unwieldy. Could also be motivated by a regulatory requirement for offline archival.

### External audit checkpointing

- **What**: Periodically write the current chain head hash to a separate trusted location (a different server, a hardware token, a third-party service). A reviewer can later confirm the current chain head matches an externally-witnessed checkpoint.
- **Why deferred**: The hash chain alone defends against in-place tampering; checkpointing defends against the harder threat of complete log replacement. For most v1 use cases, the in-place tampering defense is sufficient.
- **Trigger to revisit**: A use case where the audit log itself might be replaced by an adversary - typically high-stakes compliance environments (financial regulations, healthcare).

### Plugin system for custom rule types

- **What**: A formal mechanism for third parties to ship rule type implementations as separate packages: `pip install dataprism-ip-classifier` would add `IPRangeRule` to the policy schema and an `evaluate()` implementation.
- **Why deferred**: Plugins add complexity (entry point registration, version compatibility, isolation of plugin failures). Custom rule types can already be added today by editing dataprism itself; that's the right friction level for v1.
- **Trigger to revisit**: When two or more independent groups need to ship custom rule types without merging into dataprism.

### Multi-version policy support

- **What**: The loader supports `version: 1` policies forever, even after `version: 2` is added with different schema.
- **Why deferred**: There's no second schema version yet. Building "support multiple versions" before there are multiple versions to support is speculative.
- **Trigger to revisit**: When a schema change is needed that isn't backward-compatible. Then add version dispatch logic to the loader: read the `version` field, route to the appropriate Pydantic model, optionally upgrade old-version policies to new-version representations.

### Regulatory framework metadata per rule

- **What**: A `frameworks: list[str]` field on each rule (e.g., `frameworks: [GDPR, PDPA]`) so audit logs can be filtered by regulatory framework.
- **Why deferred**: For v1, classification labels (PII, PHI, etc.) plus operator knowledge of jurisdiction is enough. Adding `frameworks` is a non-breaking change later.
- **Trigger to revisit**: When dataprism is used in a multi-jurisdiction context (e.g., a global enterprise) and audit logs need to support framework-specific reporting.

### Performance optimizations

- **What**: Compiled-regex caching, parallel rule evaluation, batch classification, lazy iteration over large value lists.
- **Why deferred**: No measurements indicate they're needed. Premature optimization is widely known to be a mistake; speculative optimization is its quieter cousin.
- **Trigger to revisit**: When profiling shows classification is the bottleneck in a real deployment. Then optimize the specific hot path, not "everything that might be slow."

### General pattern: the YAGNI commitment

The recurring theme in this section: dataprism v1 prioritizes correctness, clarity, and reviewability over feature breadth. Each deferred item:

- Has a clear architectural path to add later
- Isn't blocked by the v1 design
- Costs real complexity if added prematurely

The discipline of saying "not yet" repeatedly is what keeps the codebase reviewable. If you're considering reviving a deferred item, the question to ask is the trigger in the relevant bullet above. If the trigger has fired, build it. If it hasn't, save the complexity for later.


## 9. Glossary

Terms used throughout this document, with short definitions. dataprism-specific meanings only - general Python concepts are not defined here.

**Actor**
The "who or what" associated with an audit event. A free-form string (e.g., `"cli"`, `"scheduler"`, `"alice@example.com"`). Recorded by every audit event; passed in by the caller.

**Adapter**
The bridge between dataprism and an external database. Defined by the `DatabaseAdapter` Protocol; v2 ships `SqliteAdapter` (test backend) and `PostgresAdapter` (production target). Future implementations will add MySQL, MSSQL, and Oracle. Adapters handle connection lifecycle (connect/close), schema introspection (list_tables/list_columns), and value sampling (sample_values).

**Audit event**
A record of something that happened in dataprism. Immutable after creation. Defined by `AuditEvent` and one of the `EventType` enum members. Persisted by an `AuditStorage` implementation.

**Audit log**
The append-only sequence of audit events for a given storage backend. The JSON Lines file (`.jsonl`) when using `JsonLinesStorage`; the in-memory list when using `InMemoryStorage`.

**Chain verification**
The process of walking the audit log and confirming each record's `prev_hash` matches the previous record's `hash`, and each record's content matches its stored hash. Implemented by `JsonLinesStorage.verify()`. Raises `ChainVerificationError` on the first detected break.

**Classification**
The act of determining what kind of sensitive data a column contains, based on policy rules. Produces one or more `ClassificationResult` objects per column.

**Classification label**
One of six fixed values (`PII`, `PHI`, `FINANCIAL`, `CREDENTIAL`, `PUBLIC`, `INTERNAL`) that describes the kind of data. Defined by the `ClassificationLabel` enum.

**Classifier**
Informal term for a single rule in a classification policy. Each entry in `policy.classifiers` is a classifier. The corresponding code model is `ClassificationRule` (the discriminated union of the three concrete rule types).

**Composition root**
The place in a codebase where dependencies are wired together. In dataprism, the composition root lives in the caller's code, not inside dataprism. The caller constructs storage, then service, then engine - in that order.

**Dialect**
SQLAlchemy's term for a database-specific SQL flavor. SQLite, PostgreSQL, MSSQL, etc. each have their own dialect. SQLAlchemy translates Core-level expressions (like `func.random()`) to the appropriate dialect-specific SQL automatically, which is what lets `SqliteAdapter` and `PostgresAdapter` share most of their implementation and lets future `MysqlAdapter`, `MssqlAdapter`, `OracleAdapter` follow the same pattern.

**Discriminated union**
A Pydantic pattern where one model field (the discriminator, here always called `type`) determines which of several concrete models the data must satisfy. For `ClassificationRule`, the discriminator selects between `RegexRule`, `DictionaryRule`, and `StatisticalRule`.

**DSN (Data Source Name)**
A database connection string. SQLAlchemy uses the format `<dialect>+<driver>://<user>:<password>@<host>:<port>/<database>`. For SQLite (no network), the simpler `sqlite:///<path>` form is used. `SqliteAdapter.connect()` accepts both a DSN string and a `Path` object, normalizing the latter to a `sqlite:///<absolute path>` DSN.

**Engine**
Subsystem-level term for the code that does meaningful work with policies and data. The Phase 1 engine is `ClassificationEngine`. Future engines (`QualityEngine`, `EncryptionEngine`) will follow the same pattern.

**Evaluator**
A function registered with `evaluate` (via `@evaluate.register`) that implements rule evaluation for one rule type. Three evaluators exist in v1: one each for `RegexRule`, `DictionaryRule`, `StatisticalRule`.

**Genesis hash**
The placeholder hash referenced by the first record in a fresh audit chain. A fixed string of 64 zero characters in v1. Has no cryptographic significance; it's a convention meaning "start of chain."

**Hash chain**
A sequence of records where each record includes the cryptographic hash of the previous record. Tampering with any past record invalidates that record's hash, breaking the chain at every subsequent record. Detectable by walking the chain and checking each link.

**Match mode**
A field on `DictionaryRule` controlling how column names are compared against the rule's values. Three modes: `EXACT` (case-sensitive equality), `EXACT_NORMALIZED` (equality after lowercase + strip separators; the default), `CONTAINS_NORMALIZED` (substring match after normalization).

**Normalization**
The text preprocessing step used by dictionary matching. Lowercases the input and strips `_`, `-`, and space characters. So `Email_Address`, `email-address`, and `Email Address` all normalize to `emailaddress`.

**Policy**
A YAML file declaring governance rules. In code: an instance of `ClassificationPolicy` produced by validating the YAML against the Pydantic schema.

**Regex target**
A field on `RegexRule` specifying what the pattern matches against. Two values: `COLUMN_NAME` (the column's name, one match attempt) or `COLUMN_VALUE` (every sampled value, all-or-nothing).

**Rule**
A single entry in `policy.classifiers`. One of three concrete types in v1: `RegexRule`, `DictionaryRule`, or `StatisticalRule`. Each describes a pattern for identifying data and the classification label to assign.

**SampledValues**
The return type of `DatabaseAdapter.sample_values()`. A frozen dataclass carrying both `text` (stringified values with NULLs filtered out, for classification) and `typed` (native Python types with NULLs preserved as None, for the future quality engine). Also includes `null_count`, `sample_size_requested`, and `sample_size_actual` for visibility into what was actually sampled.

**Sampling strategy**
How an adapter chooses which N values to return from a column. Two strategies in v2: `SEQUENTIAL` (the first N rows, deterministic and fast - the default) and `RANDOM` (a random sample, slower but statistically representative). Defined by the `SamplingStrategy` enum in `dataprism.adapters.protocol`.

**Single dispatch**
Python's mechanism (`functools.singledispatch`) for selecting a function implementation based on the runtime type of its first argument. dataprism uses it for rule evaluation: `evaluate(rule, ...)` dispatches to one of three registered functions depending on `type(rule)`.

**Storage backend**
An implementation of the `AuditStorage` protocol. v1 ships `InMemoryStorage` (for tests) and `JsonLinesStorage` (for production). Other backends (PostgreSQL, S3, etc.) could be added by satisfying the same protocol.

**Strict mode**
The Pydantic configuration `extra="forbid"`, applied to every model in dataprism. Unknown fields in input data are rejected, not silently ignored. The opposite of strict mode is `extra="ignore"`, which dataprism deliberately doesn't use.

**Subsystem**
A logical unit of dataprism's architecture: audit, policy, or classification. Each subsystem corresponds to a Python subpackage (`dataprism.audit`, `dataprism.policy`, `dataprism.classification`) and has its own purpose, public API, and exception hierarchy.

**Tamper-evidence**
The property that modifications to past records leave detectable evidence. Distinct from tamper-prevention (preventing modification in the first place). dataprism's hash chain provides tamper-evidence, not tamper-prevention; the practical difference is that detection happens at verify time, not at modification time.
