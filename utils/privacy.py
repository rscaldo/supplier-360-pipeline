"""
privacy.py — CNPJ pseudonymisation utilities (ADR-005).

Implements HMAC-SHA256 tokenisation for CNPJ fields exposed in the
Gold and Serving layers.

LGPD context
------------
MEI (Microempreendedor Individual) and EI (Empresário Individual) CNPJs
are personal data under LGPD because they uniquely identify natural
persons. Pseudonymisation with a secret salt prevents reversal by anyone
who does not hold the salt, satisfying the LGPD requirement for
minimisation of personal data exposure in analytical layers.

Security properties
-------------------
- HMAC-SHA256 with a 256-bit random salt is computationally irreversible
  without the salt (unlike bare SHA-256, which is vulnerable to brute-force
  over the 14-digit CNPJ space).
- The salt is loaded from the environment — never stored in code or logs.
- The ``_salt_bytes`` attribute is not exposed as a public property.

Environment
-----------
Set ``CNPJ_SALT`` in ``local/.env`` (never commit to Git):
    CNPJ_SALT=<64-char random hex>  # generate: python -c "import secrets; print(secrets.token_hex(32))"
"""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any, Sequence


class CNPJTokeniser:
    """
    Compute HMAC-SHA256 tokens for CNPJ pseudonymisation.

    The salt is loaded once at instantiation and stored only as bytes.
    It is never written to any output file, log, or string representation.

    Parameters
    ----------
    salt : secret salt string. If omitted, reads ``CNPJ_SALT`` from the
           environment. Raises ``EnvironmentError`` if absent or empty.

    Raises
    ------
    EnvironmentError
        If no salt is provided and ``CNPJ_SALT`` is not set in the environment.

    Examples
    --------
    >>> tokeniser = CNPJTokeniser()
    >>> token = tokeniser.tokenise("00000000000191")
    >>> tokens = tokeniser.tokenise_batch(["00000000000191", "11111111000191"])
    >>> pairs = tokeniser.tokenise_pairs([("00000000000191", 42)])
    """

    def __init__(self, salt: str | None = None) -> None:
        if salt is None:
            salt = os.getenv("CNPJ_SALT")
        if not salt:
            raise EnvironmentError(
                "CNPJ_SALT is not set.\n"
                "Add it to your local/.env file:\n"
                "  CNPJ_SALT=<64-char random hex>\n"
                "Generate one: python -c \"import secrets; print(secrets.token_hex(32))\"\n"
                "Never commit the salt to Git — add .env to .gitignore."
            )
        # Store as bytes — never expose as a string attribute
        self.__salt_bytes = salt.encode("utf-8")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def tokenise(self, cnpj: str) -> str:
        """
        Compute the HMAC-SHA256 token for a single CNPJ.

        Parameters
        ----------
        cnpj : 14-character normalised CNPJ string (digits only, no punctuation).

        Returns
        -------
        str
            64-character lowercase hex string.

        Notes
        -----
        The same CNPJ + same salt always produces the same token (deterministic).
        Different CNPJs always produce different tokens (collision-resistant).
        """
        return hmac.new(
            self.__salt_bytes,
            cnpj.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def tokenise_batch(self, cnpjs: Sequence[str]) -> list[str]:
        """
        Compute tokens for a sequence of CNPJs.

        Parameters
        ----------
        cnpjs : sequence of 14-character normalised CNPJ strings.

        Returns
        -------
        list[str]
            Tokens in the same order as the input.
        """
        return [self.tokenise(cnpj) for cnpj in cnpjs]

    def tokenise_pairs(
        self,
        cnpj_key_pairs: Sequence[tuple[str, Any]],
    ) -> list[tuple[str, Any]]:
        """
        Compute tokens for (cnpj, key) pairs.

        Used in serving notebooks to build the token mapping table
        without retaining cnpj_normalized in any intermediate structure.

        Parameters
        ----------
        cnpj_key_pairs : sequence of (cnpj_normalized, any_key) tuples.
                         The key is typically ``supplier_sk``.

        Returns
        -------
        list[tuple[str, Any]]
            (cnpj_token, key) pairs in the same order as the input.

        Examples
        --------
        >>> pairs = con.execute("SELECT cnpj_normalized, supplier_sk FROM v_dim").fetchall()
        >>> token_rows = tokeniser.tokenise_pairs(pairs)
        >>> # token_rows → [('3f4a8b...', 1), ('a2c9d1...', 2), ...]
        """
        return [(self.tokenise(cnpj), key) for cnpj, key in cnpj_key_pairs]

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        # Never include the actual salt value in repr
        return "CNPJTokeniser(salt=<redacted>)"
