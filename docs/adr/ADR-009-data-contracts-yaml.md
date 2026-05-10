# ADR-009 — Data Contracts YAML + datacontract-cli

## Status
Accepted

## Date
2026-05-09

## Context

Quality checks embedded in pipeline notebooks validate data at ingestion time.
But they answer a different question from what a Data Contract answers:

- **Pipeline quality checks**: "is the data good enough to proceed?"
- **Data Contract**: "what does this table promise to its consumers — and is
  that promise currently being kept?"

A Data Contract is a formal, versioned agreement between the producer
(this pipeline) and the consumer (dashboard, ML model, analyst).
It defines: schema, field types, required fields, and quality rules.
It is machine-validated — not just documentation.

Without Data Contracts, schema changes are discovered by consumers at
query time. With Data Contracts, any breaking change is detected
automatically in CI/CD before consumers are affected.

## Decision

All **Gold** and **Serving** tables have ODCS v3.1.0 Data Contracts
validated with `datacontract-cli`.

Contract files are stored in `contracts/gold/` and `contracts/serving/`,
versioned in Git alongside the pipeline code.

Validation runs against local Parquet files using the DuckDB engine:

```bash
datacontract test contracts/gold/dim_fornecedor.odcs.yaml
```

### ODCS v3.1.0 implementation notes

Two critical distinctions discovered during implementation:

1. **`logicalType` vs `physicalType`**: `logicalType` is validated by
   `datacontract lint` (ODCS standard). `physicalType` is used by the
   DuckDB engine during `datacontract test`. Both must be specified.

2. **DuckDB physicalType mapping**:

| DuckDB type | physicalType in YAML |
|---|---|
| `VARCHAR` | `varchar` |
| `INTEGER` | `integer` |
| `BIGINT` | `bigint` |
| `BOOLEAN` | `boolean` |
| `DATE` | `date` |
| `TIMESTAMP` | `timestamp` |
| `TIMESTAMP WITH TIME ZONE` | `timestamp_tz` |
| `DECIMAL(p,s)` | `decimal(p,s)` |
| `DOUBLE` | `double` |

`timestamp with time zone` as a string is not recognized by the DuckDB
`sql_type_converter` — it returns `None` and breaks the generated SQL.
The correct value is `timestamp_tz`. This was confirmed by reading the
`datacontract-cli` source code directly.

## Contracts and check counts

| Contract | Table | Checks |
|---|---|---|
| `dim_tempo.odcs.yaml` | Gold | 30 |
| `dim_fornecedor.odcs.yaml` | Gold | 28 |
| `fato_contratos.odcs.yaml` | Gold | 42 |
| `fato_sancoes.odcs.yaml` | Gold | 42 |
| `serving_fornecedor_perfil.odcs.yaml` | Serving | 50 |
| `serving_fornecedor_features.odcs.yaml` | Serving | 40 |
| **Total** | | **232** |

All 232 checks passing as of 2026-05-09.

## Options Considered

| Option | Description | Decision |
|---|---|---|
| No formal contracts | Quality checks only in notebooks | Rejected — no consumer-facing guarantee |
| dbt tests | Schema tests in dbt | Rejected — dbt not in stack |
| Great Expectations | Python-native quality framework | Rejected — heavy setup for this use case |
| **ODCS + datacontract-cli (chosen)** | Open standard, CLI validation, Git-friendly | Accepted |

## Consequences

- `contracts/` folder is versioned in Git — contract changes are tracked
  alongside schema changes
- `nbstripout` git hook prevents notebook outputs from polluting the repository
- Breaking schema changes will fail `datacontract test` — detectable before
  promoting to the Databricks phase
- `datacontract-cli[duckdb]` must be installed: `pip install 'datacontract-cli[duckdb]'`

## References

- [datacontract-cli documentation](https://cli.datacontract.com)
- [Open Data Contract Standard (ODCS)](https://datacontract.com)
- ADR-008 — Two consumer-aligned Serving tables (each has its own contract)
