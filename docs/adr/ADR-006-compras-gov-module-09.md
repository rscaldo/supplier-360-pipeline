# ADR-006 — Compras.gov Module 09 (Contracts) for MVP

## Status
Accepted

## Date
2026-04-19

## Context

The Compras.gov public API (`dadosabertos.compras.gov.br`) offers 11 data
modules covering different aspects of Brazilian federal procurement.

The project needs to define which module(s) to include in the MVP scope.
Including all 11 modules would significantly increase pipeline complexity,
data volume, and maintenance surface without proportional analytical value
for the core use case: supplier due diligence.

## Options Considered

| Module | Description | Decision |
|---|---|---|
| 06 | Legacy procurement (Lei 8.666/1993) | Rejected — outside temporal scope |
| 07 | PNCP contracts (Lei 14.133/2021) | Rejected — duplicates PNCP source |
| **09** | **Contracts — execution data with supplier CNPJ** | **Selected** |
| 10 | Supplier registry (SICAF) — size, CNAE, qualification | Deferred to future phase |
| Others | Bidding, items, prices | Out of scope for MVP |

## Decision

**Module 09 (Contracts)** is the sole Compras.gov source for the MVP.

Module 09 provides contract execution data with:
- Supplier CNPJ (`niFornecedor`) — enables cross-source join
- Buyer organization code (`codigoOrgao`) — stored as `VARCHAR` (API returns string,
  despite specification claiming integer)
- Contract value, dates, and object description
- Managing unit (`codigoUnidadeGestora`) — required for correct grain definition

Module 10 (Supplier/SICAF) is documented as a planned extension — it enriches
the supplier profile with qualification data but does not change the core
analytical capability.

## Grain decision

The `fato_contratos` grain is: one row per contract per managing unit
(`codigo_unidade_gestora`). The same contract number can appear across different
organizational units, amendment values, and renewal dates — six fields are
required to uniquely identify a row.

## Consequences

- Bootstrap covers 2021-01 to current month — ~711K contracts historically
- `niFornecedor` is the join key with PNCP and Portal da Transparência sources
- `codigoOrgao` stored as VARCHAR — API returns `"170105"` not `170105`
- 406 records with `tipoPessoa='PJ'` contained CPF identifiers (11 digits) —
  filtered at Silver with `AND length(niFornecedor) = 14`
- Module 10 (SICAF) deferred — no schema changes required to add it later

## References

- [Compras.gov API documentation](https://dadosabertos.compras.gov.br/swagger-ui/index.html)
- ADR-002 — CNPJ as VARCHAR (applies to `niFornecedor`)
