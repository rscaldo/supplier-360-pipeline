# ADR-007 — silver_identidade Filtered to Active CNPJs Only

## Status
Accepted

## Date
2026-05-07

## Context

The Receita Federal CNPJ registry contains **70M+ records** — the full
universe of Brazilian business registrations, including entities that have
never appeared in any procurement or sanctions activity.

Processing all 70M records through the Silver → Gold → Serving pipeline
locally is not feasible:
- `dim_fornecedor` with SCD2 over 70M rows requires hours of processing
- HMAC tokenization of 70M CNPJs would exhaust local memory
- The analytical question this pipeline answers is: "what is the profile
  of suppliers that interact with the federal government?" — not "what is
  the profile of all registered businesses in Brazil?"

A supplier that has never signed a government contract and has no sanctions
record is irrelevant to the use case. Including it adds cost without adding
value.

## Decision

`silver_identidade` is filtered to retain only CNPJs that appear in at least
one of the procurement or sanctions sources:

```sql
-- Active CNPJ set: union of all source CNPJs
CREATE TABLE cnpjs_ativos AS
SELECT DISTINCT cnpj_basico FROM silver_ceis
UNION
SELECT DISTINCT cnpj_basico FROM silver_cnep
UNION
SELECT DISTINCT cnpj_basico FROM silver_compras
UNION
SELECT DISTINCT cnpj_basico FROM silver_pncp;

-- Apply filter at Silver
SELECT * FROM silver_identidade_full
WHERE cnpj_basico IN (SELECT cnpj_basico FROM cnpjs_ativos)
```

`cnpjs_ativos` is always rebuilt from scratch (no skip) to ensure new CNPJs
from updated source files are captured in reprocessing runs.

## Filtering results

| Metric | Value |
|---|---|
| Full Receita Federal registry | 70M+ CNPJs |
| Active CNPJs (in contracts or sanctions) | ~810,921 |
| Reduction factor | ~369x |
| Coverage of contract/sanctions sources | 99.992% |

33 unmatched CNPJ roots were identified as malformed source data
(e.g., `54670S76` with a letter in the root) or sentinel values
(e.g., `99999999`). These are documented as known data quality issues —
not pipeline bugs.

## Bronze layer

Bronze retains the **complete, unfiltered** Receita Federal dataset.
The filter is applied exclusively at Silver. This preserves raw data
integrity and allows future analysis at full scale if needed.

## Consequences

- Local pipeline runtime: ~15 minutes (vs. estimated hours at full scale)
- `silver_identidade` partitioned by `uf` (27 partitions) for efficient
  predicate pushdown
- The `cnpjs_ativos` table must be rebuilt before `silver_identidade`
  in every pipeline run — order dependency is intentional
- Quality check uses `>= 99.99%` coverage threshold instead of exact count
  equality — accounts for known malformed CNPJs in source data

## References

- ADR-001 — Local-first development (cost constraint that motivated this decision)
- ADR-004 — SCD Type 2 (downstream consumer of silver_identidade)
