# ADR-003 — Temporal Join on dim_fornecedor

## Status
Accepted

## Date
2026-04-28

## Context

`dim_fornecedor` implements SCD Type 2 historization (see ADR-004).
Multiple versions of the same supplier may exist simultaneously in the table,
distinguished by `valid_from`, `valid_to`, and `is_current` fields.

A naive join pattern using `is_current = true` is tempting but incorrect:

```sql
-- INCORRECT — never use this pattern
SELECT *
FROM fato_contratos f
JOIN dim_fornecedor d ON f.supplier_sk = d.supplier_sk
WHERE d.is_current = true
```

This pattern assigns the **current** supplier attributes to all historical
contracts, regardless of when the contract was signed. If a supplier changed
legal status, size classification, or address between 2021 and 2026, the join
would attribute today's state to contracts signed under a different state.

The correct pattern joins on the supplier version **active at the time of the
contract reference date**:

```sql
-- CORRECT — temporal join
SELECT *
FROM fato_contratos f
JOIN dim_fornecedor d
  ON f.supplier_sk = d.supplier_sk
 AND f.data_referencia >= d.valid_from
 AND f.data_referencia <  COALESCE(d.valid_to, '9999-12-31')
```

## Decision

All joins between fact tables and `dim_fornecedor` must use the temporal join
pattern — matching on `supplier_sk` plus a date range check against
`valid_from` / `valid_to`.

Using `is_current = true` alone is explicitly prohibited in this pipeline.

## Options Considered

| Option | Correctness | Risk |
|---|---|---|
| `is_current = true` | Incorrect for historical analysis | Silently attributes wrong supplier state to past contracts |
| **Temporal join on date range (chosen)** | Correct | Slightly more complex query |
| Snapshot table per period | Correct | Expensive storage; overkill for this use case |

## Consequences

- `fato_contratos` and `fato_sancoes` carry `data_referencia` as the temporal
  anchor for dimension lookups
- `dim_fornecedor` retains `is_current` as a convenience flag for
  point-in-time queries only — never as a join predicate
- Serving layer (`serving_fornecedor_perfil`) joins on `is_current = true`
  intentionally — it represents the current supplier state for dashboard display,
  not a historical analysis

## References

- Kimball, R. — *The Data Warehouse Toolkit*, Chapter on Slowly Changing Dimensions
- ADR-004 — SCD Type 2 with hash change detection
