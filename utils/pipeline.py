"""
pipeline.py — Pipeline coordination utilities.

Provides functions for enforcing execution order between pipeline notebooks
and coordinating state between steps.

Design principles
-----------------
- Every Bronze notebook calls check_landing_gate() in Step 1.
- Fail fast and explicitly — never silently process incomplete data.
- On Databricks: replace raise with dbutils.notebook.exit() for DAB control.
"""
from __future__ import annotations

import json
from pathlib import Path


def check_landing_gate(project_root: Path) -> None:
    """
    Verify that the landing gate passed before running a Bronze notebook.

    Call this in Step 1 of every Bronze notebook (06 onwards).
    Raises RuntimeError immediately if the gate did not pass, preventing
    Bronze from processing incomplete or corrupted raw data.

    Parameters
    ----------
    project_root : PROJECT_ROOT path (from get_project_root())

    Raises
    ------
    RuntimeError
        If the gate file does not exist or status is not 'SUCCESS'.

    Notes
    -----
    On Databricks, replace the raise statements with:
        dbutils.notebook.exit(json.dumps({"status": "FAILED", "reason": msg}))
    so the DAB orchestrator can control the workflow.

    Examples
    --------
    >>> from utils.paths import get_project_root
    >>> from utils.pipeline import check_landing_gate
    >>> PROJECT_ROOT = get_project_root()
    >>> check_landing_gate(PROJECT_ROOT)  # raises if gate failed
    >>> print("Landing gate OK — proceeding")
    """
    gate_path = project_root / "db" / "landing_gate.json"

    if not gate_path.exists():
        raise RuntimeError(
            "Landing gate file not found.\n"
            "Run 05_landing_validate.ipynb before any Bronze notebook.\n"
            f"Expected: {gate_path}"
        )

    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    status = gate.get("status", "UNKNOWN")

    if status != "SUCCESS":
        failed = gate.get("failed_checks", [])
        finished = gate.get("finished_at", "unknown")
        raise RuntimeError(
            f"Landing gate FAILED (last run: {finished}).\n"
            f"Failed checks: {failed}\n"
            "Fix the issues and re-run 05_landing_validate.ipynb."
        )

    finished = gate.get("finished_at", "unknown")
    print(f"Landing gate: OK (passed at {finished})")
