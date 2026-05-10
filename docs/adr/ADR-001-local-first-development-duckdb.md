# ADR-001 — Local-first Development with DuckDB

## Status
Accepted

## Date
2026-04-19

## Context

Building and validating pipeline logic directly against a cloud platform
(Azure Databricks) introduces two problems:

1. **Cost**: an all-purpose Databricks cluster incurs DBU charges even while
   the developer is writing, debugging, or iterating on transformation logic.

2. **Feedback loop**: cloud execution cycles are slow. A failed notebook
   requires re-attaching to a cluster, re-running all upstream cells, and
   waiting for distributed execution to surface the error.

The pipeline processes data from four Brazilian federal government sources
totalling 70M+ records at full scale. Validating transformation logic at
that scale locally — before any cloud execution — is a cost control
requirement, not just a convenience.

## Decision

All pipeline logic is developed and validated locally using **DuckDB** against
**Parquet files** before any cloud promotion.

- Bronze, Silver, Gold, and Serving notebooks are written and tested locally first
- DuckDB reads partitioned Parquet files directly — no server required
- Local execution validates: schema, transformation logic, quality checks, and
  data contracts
- Cloud promotion happens only after local validation passes

The local environment is strictly reserved for development and EDA.
It is not a parallel production environment.

## Options Considered

| Option | Pros | Cons |
|---|---|---|
| Develop directly on Databricks | Production parity | High cost, slow feedback loop |
| Local PySpark | Close to Databricks runtime | Requires Java, complex setup |
| **DuckDB + Parquet (chosen)** | Fast, zero-config, SQL-native, free | Requires porting to PySpark for cloud |
| pandas only | Simple | Does not scale to 70M+ rows |

## Consequences

- `data/` folder is in `.gitignore` — raw and processed data never enter the repository
- `db/` folder is in `.gitignore` — DuckDB database files never enter the repository
- All notebooks run against local Parquet files via `read_parquet()`
- Pipeline promotion to Databricks requires translating DuckDB SQL to PySpark/Delta Lake
- `utils/` shared library is written in pure Python — compatible with both environments

## References

- [DuckDB documentation](https://duckdb.org/docs/)
- ADR-005 (Databricks project) — Spot instances with on-demand fallback
