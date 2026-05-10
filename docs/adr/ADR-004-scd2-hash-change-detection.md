# ADR-004 — SCD Type 2 with Hash Change Detection

## Status
Accepted

## Date
2026-04-28

## Context

`dim_fornecedor` is built from `silver_identidade`, which is derived from
the Receita Federal CNPJ registry. Supplier attributes — legal status,
size classification, address, legal nature — can change over time.

For procurement analysis, it matters which state a supplier was in **at the
time of a contract** — not just their current state. This requires tracking
attribute history.

Two sub-decisions are required:
1. Which historization strategy to use (SCD Type 1, 2, or 3)
2. How to detect attribute changes efficiently

For change detection, a field-by-field comparison across 6+ tracked attributes
is verbose and error-prone. A hash over the tracked attributes enables
single-column comparison.

The surrogate key (`supplier_sk`) must be stable across reloads of the same
CNPJ version. A global sort-based `ROW_NUMBER()` over 810K rows causes
`OutOfMemoryException` in DuckDB when combined with other blocking operators.

## Decision

**SCD Type 2** is applied selectively to `dim_fornecedor`.

Tracked attributes (trigger a new version when changed):
- `razao_social`
- `porte`
- `situacao_cadastral`
- `natureza_juridica_desc`
- `uf`
- `municipio_desc`

Non-tracked attributes (overwritten in place):
- `tem_sancao` — updated at each pipeline run without creating a new version

**Change detection**: MD5 hash over concatenated tracked attributes stored
as `_attr_hash`. A new SCD2 version is created when `_attr_hash` differs
from the previous version.

**Surrogate key**: `MD5(cnpj_normalized || valid_from::VARCHAR)` — deterministic
and stable across reloads without requiring a global sort.

## Options Considered

| Option | Description | Decision |
|---|---|---|
| SCD Type 1 | Overwrite current values | Loses history — rejected |
| **SCD Type 2 (chosen)** | New row per change | Preserves full history |
| SCD Type 3 | Previous + current column | Limited to one prior version — rejected |
| ROW_NUMBER() global sort as SK | Sequential integer SK | Causes OOM in DuckDB over 800K rows — rejected |
| **MD5(cnpj + valid_from) as SK (chosen)** | Deterministic hash | Stable, no sort required |

## Consequences

- `dim_fornecedor` has 810,921 rows at first load (no prior history — all records
  have `valid_from = pipeline run date`, `valid_to = NULL`, `is_current = true`)
- Incremental loads will create new rows when `_attr_hash` changes
- `supplier_sk` is a VARCHAR MD5 hash — not a sequential integer
- Fact tables join on `supplier_sk` using the temporal pattern (ADR-003)
- `tem_sancao` is excluded from the hash — sanctions state is volatile and
  creating a new dimension version per sanctions change would inflate the table

## References

- ADR-003 — Temporal join pattern
- ADR-002 — CNPJ as VARCHAR (used in surrogate key generation)
