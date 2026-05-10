"""
validation.py — CheckSuite: PASS/FAIL framework for notebook validation.

Provides a consistent, structured way to declare and report data quality
checks across all Bronze, Silver, Gold and Serving notebooks.

Design principles
-----------------
- Declarative: add checks with .add(), report separately with .report().
- Non-blocking by default: all checks run even if early ones fail.
- Explicit failure: .assert_all_pass() raises AssertionError if any failed.
- Readable output: fixed-width columns, clear PASS/FAIL markers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CheckResult:
    """Immutable result of a single validation check."""
    name:   str
    passed: bool
    detail: str


class CheckSuite:
    """
    Accumulates validation checks and produces a structured report.

    Parameters
    ----------
    title : human-readable name of the artefact being validated,
            used in the summary line (e.g. ``'silver_identidade'``).

    Examples
    --------
    >>> suite = CheckSuite("silver_ceis")
    >>> suite.add("Row count", total == 13562, f"{total:,} rows")
    >>> suite.add("No null cnpj", null_cnpj == 0, f"{null_cnpj} nulls")
    >>> suite.add("cnpj length 14", wrong_len == 0, f"{wrong_len} wrong length")
    >>> suite.report()
    >>> suite.assert_all_pass()
    """

    _COL_WIDTH = 52   # left column width in characters

    def __init__(self, title: str = "Validation") -> None:
        self.title  = title
        self._checks: list[CheckResult] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, name: str, condition: bool, detail: Any = "") -> None:
        """
        Register a single validation check.

        Parameters
        ----------
        name      : human-readable description (≤ 52 chars recommended).
        condition : ``True`` → PASS, ``False`` → FAIL.
        detail    : value or message shown in the right column.
                    Accepts any type — converted to str automatically.
        """
        self._checks.append(
            CheckResult(name=name, passed=bool(condition), detail=str(detail))
        )

    def report(self) -> bool:
        """
        Print the check results table to stdout.

        Returns
        -------
        bool
            ``True`` if every check passed, ``False`` otherwise.
        """
        w = self._COL_WIDTH
        print(f"\n{'CHECK':<{w}} {'STATUS':<8} DETAIL")
        print("-" * (w + 32))

        all_pass = True
        for c in self._checks:
            status = "PASS" if c.passed else "FAIL"
            print(f"{c.name:<{w}} [{status}]   {c.detail}")
            if not c.passed:
                all_pass = False

        print()
        if all_pass:
            print(f"All checks PASSED — {self.title} ready.")
        else:
            failed_names = [c.name for c in self._checks if not c.passed]
            n = len(failed_names)
            print(f"{n} check(s) FAILED: {', '.join(failed_names)}")

        return all_pass

    def assert_all_pass(self) -> None:
        """
        Raise ``AssertionError`` if any check failed.

        Call this after ``.report()`` to halt notebook execution on failure.

        Raises
        ------
        AssertionError
            Contains the names of all failed checks.
        """
        failed = [c.name for c in self._checks if not c.passed]
        if failed:
            raise AssertionError(
                f"Validation failed ({len(failed)} check(s)): "
                + ", ".join(failed)
            )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def all_passed(self) -> bool:
        """``True`` if every registered check passed."""
        return all(c.passed for c in self._checks)

    @property
    def failed_checks(self) -> list[CheckResult]:
        """List of checks that did not pass."""
        return [c for c in self._checks if not c.passed]

    @property
    def passed_checks(self) -> list[CheckResult]:
        """List of checks that passed."""
        return [c for c in self._checks if c.passed]

    def __len__(self) -> int:
        return len(self._checks)

    def __repr__(self) -> str:
        total  = len(self._checks)
        passed = sum(1 for c in self._checks if c.passed)
        return f"CheckSuite(title={self.title!r}, {passed}/{total} passed)"
