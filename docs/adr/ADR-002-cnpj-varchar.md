# ADR-002 — CNPJ Stored as VARCHAR

## Status
Accepted

## Date
2026-04-14

## Context

The Brazilian CNPJ (Cadastro Nacional da Pessoa Jurídica) is the primary
business identifier used across all four data sources in this pipeline.

Two storage choices exist: `INTEGER` or `VARCHAR`.

The Brazilian federal government announced a migration of the CNPJ format
from purely numeric (14 digits) to alphanumeric (effective July 2026).
Under the new format, a CNPJ may contain letters in positions 1–8,
making `INTEGER` storage permanently incompatible.

Additionally, the CNPJ has semantic structure (CNPJ root = first 8 digits,
branch = digits 9–12, check digits = 13–14) that must be preserved exactly,
including leading zeros. `INTEGER` truncates leading zeros silently.

## Decision

CNPJ is stored as `VARCHAR` across all pipeline layers.

Three canonical fields are defined per layer:

| Field | Layer | Description |
|---|---|---|
| `cnpj_raw` | Bronze | Exactly as received from the source — no normalization |
| `cnpj_normalized` | Silver / Gold | Digits only, zero-padded to 14 characters |
| `cnpj_token` | Gold / Serving | HMAC-SHA256 pseudonymized — see ADR-005 |

Normalization expression (DuckDB):
```sql
LPAD(REGEXP_REPLACE(cnpj_raw, '[^0-9A-Za-z]', '', 'g'), 14, '0')
```

## Options Considered

| Option | Pros | Cons |
|---|---|---|
| INTEGER | Compact, fast comparison | Truncates leading zeros; incompatible with alphanumeric migration |
| **VARCHAR (chosen)** | Future-proof; preserves structure | Slightly larger storage footprint |

## Consequences

- All JOIN operations on CNPJ use normalized VARCHAR comparison
- Bronze retains `cnpj_raw` — normalization happens at Silver
- 406 PNCP records with `tipoPessoa='PJ'` contained CPF (11-digit) identifiers —
  filtered with `AND length(niFornecedor) = 14` at Silver ingestion
- The `REGEXP_REPLACE` flag `'g'` is required in DuckDB for global replacement
  (default replaces only the first match)

## References

- [Receita Federal — CNPJ alphanumeric migration announcement](https://www.gov.br/receitafederal/)
- ADR-005 — HMAC-SHA256 pseudonymization (cnpj_token)
