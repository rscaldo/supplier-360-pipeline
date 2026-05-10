# ADR-008 — Serving Layer: Two Consumer-Aligned Tables

## Status
Accepted

## Date
2026-05-08

## Context

The Serving layer must serve two consumers with fundamentally different
data requirements:

**Consumer 1 — Dashboard / supplier lookup**
- Input: a CNPJ token (or supplier identifier)
- Output: a complete supplier profile — name, size, legal nature, address,
  contract history summary, sanctions summary
- Requires: descriptive fields, human-readable labels, aggregated metrics
- Key: `cnpj_token` (LGPD-compliant identifier for lookup)

**Consumer 2 — H2O Driverless AI (ML classification)**
- Input: the full supplier population
- Output: a flat feature matrix for binary classification (sanctioned / not sanctioned)
- Requires: numeric and categorical features, no high-cardinality text fields,
  no nested structures
- Key: `supplier_sk` (no CNPJ exposure needed for ML)

A single Serving table cannot serve both consumers well:
- Dashboard needs `razao_social`, `municipio_desc`, `natureza_juridica_desc` —
  high-cardinality text that degrades ML model quality
- ML needs binary flags (`tem_sancao`, `tem_ceis`, `tem_cnep`) and numeric
  aggregates — fields that add no value to a dashboard display

Coupling both schemas into one table creates an antipattern: every schema
evolution request from one consumer risks breaking the other.

## Decision

Two independent consumer-aligned Serving tables derived from the same Gold layer:

### serving_fornecedor_perfil
Dashboard and CNPJ lookup use case.

Key fields: `cnpj_token`, `supplier_sk`, `razao_social`, `porte`,
`situacao_cadastral`, `natureza_juridica_desc`, `uf`, `municipio_desc`,
`tem_sancao`, `scd2_valid_from`, contract aggregates, sanctions aggregates.

### serving_fornecedor_features
ML feature table for H2O Driverless AI classification.

Key fields: `supplier_sk`, categorical dimensions (`porte`, `natureza_juridica_desc`,
`uf`, `situacao_cadastral`), numeric features (`qtd_contratos`, `valor_total_contratos`,
`valor_medio_contrato`), binary flags (`tem_sancao`, `tem_ceis`, `tem_cnep`,
`sancao_ativa`).

No CNPJ or name fields — ML model learns patterns, not identities.

## Risk scoring

A `gold_regras_atencao` table with explicit risk scoring rules was considered
but deferred. Rule-based risk scoring belongs to the data science phase —
not to the data engineering MVP. H2O Driverless AI will produce a supervised
classification model in a future phase.

## Consequences

- Both tables have independent Data Contracts (ADR-009) — schema evolution
  is decoupled
- `serving_fornecedor_perfil` exposes `cnpj_token` — LGPD compliant (ADR-005)
- `serving_fornecedor_features` exposes only `supplier_sk` — no personal data
- Both tables contain 810,921 rows (one row per current supplier)
- Future ML phase: H2O Driverless AI reads `serving_fornecedor_features`
  as input; target variable is `tem_sancao`

## References

- Zhamak Dehghani — *Data Mesh* (consumer-aligned data products principle)
- ADR-005 — HMAC-SHA256 pseudonymization (`cnpj_token`)
- ADR-009 — Data Contracts for Gold and Serving tables
