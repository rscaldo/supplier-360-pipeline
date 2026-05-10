# ADR-005 — HMAC-SHA256 CNPJ Pseudonymization (LGPD / GDPR)

## Status
Accepted

## Date
2026-04-19

## Context

Brazil's Lei Geral de Proteção de Dados (LGPD) — and by principle the EU's
General Data Protection Regulation (GDPR) — classify identifiers that
uniquely identify natural persons as personal data requiring protection.

Two CNPJ categories in this dataset identify natural persons:

- **MEI** (Microempreendedor Individual): a simplified business registration
  used exclusively by individual entrepreneurs. The CNPJ is unique to the person.
- **EI** (Empresário Individual): a sole proprietorship where the business
  identity is legally inseparable from the individual.

Exposing raw MEI/EI CNPJs in analytical layers violates the data minimization
principle under both LGPD (Art. 6, VI) and GDPR (Art. 5(1)(c)).

**Why SHA-256 alone is insufficient**: the CNPJ space is 14 digits —
approximately 10^14 possible values, but with structural constraints the
effective space is much smaller. A bare SHA-256 hash is vulnerable to
brute-force enumeration: an attacker with the hash can iterate over all
valid CNPJs (feasible in minutes on modern hardware) and reverse the mapping.

## Decision

All CNPJ fields exposed in the **Gold** and **Serving** layers are
pseudonymized using **HMAC-SHA256 with a secret salt**:

```python
import hmac, hashlib

token = hmac.new(
    salt_bytes,
    cnpj.encode("utf-8"),
    hashlib.sha256,
).hexdigest()
```

Properties of this approach:
- **Deterministic**: the same CNPJ + same salt always produces the same token
- **Irreversible without the salt**: computationally infeasible to reverse
- **Collision-resistant**: different CNPJs produce different tokens

The salt is:
- A 256-bit random value (32 bytes, hex-encoded to 64 characters)
- Loaded exclusively from the environment variable `CNPJ_SALT`
- Never stored in code, logs, version control, or pipeline outputs

Implementation: `utils/privacy.py` — `CNPJTokeniser` class.

## Field mapping by layer

| Layer | Field | Value |
|---|---|---|
| Bronze | `cnpj_raw` | Exactly as received from source |
| Silver | `cnpj_normalized` | Digits only, zero-padded to 14 chars |
| Gold | `cnpj_normalized` | Present but access-controlled |
| Gold / Serving | `cnpj_token` | HMAC-SHA256 token — exposed to consumers |

## Options Considered

| Option | Security | Decision |
|---|---|---|
| No pseudonymization | None | Rejected — LGPD violation |
| SHA-256 without salt | Weak — brute-forceable | Rejected |
| AES encryption | Reversible with key | Rejected — reversibility is a liability |
| **HMAC-SHA256 + salt (chosen)** | Strong — irreversible without salt | Accepted |
| Tokenization with lookup table | Strong | Rejected — lookup table itself is a liability |

## Consequences

- `cnpj_token` is the business key in Gold and Serving layers
- `cnpj_normalized` is present in Gold for pipeline joins but masked for
  external consumers
- The `CNPJ_SALT` environment variable must be set before running any
  Gold or Serving notebook
- Lost salt = lost ability to regenerate tokens consistently —
  salt must be backed up securely
- `utils/privacy.py` `__repr__` explicitly redacts the salt:
  `CNPJTokeniser(salt=<redacted>)`

## References

- LGPD Art. 6 (data minimization), Art. 12 (anonymization)
- GDPR Art. 4(5) (pseudonymization definition), Art. 5(1)(c) (data minimization)
- [HMAC — RFC 2104](https://www.rfc-editor.org/rfc/rfc2104)
- ADR-002 — CNPJ as VARCHAR
- `utils/privacy.py`
