"""
bootstrap_log.py — Structured logging shared across all bootstrap scripts.

All four bootstrap scripts (receita_federal, pncp, transparencia, compras)
write to the same ``bootstrap_log.json`` file using this module.

Log entry schema
----------------
source_id     : str  — e.g. 'compras_gov', 'pncp', 'transparencia', 'receita_federal'
period        : str  — 'YYYY-MM' for monthly sources, 'static' for full-reload sources
status        : str  — SUCCESS | EMPTY | ERROR  (SKIPPED is never written)
records       : int  — number of records written to the output file
bytes_written : int  — output file size in bytes
started_at    : str  — ISO 8601 UTC timestamp
finished_at   : str  — ISO 8601 UTC timestamp
error_message : str  — populated only on ERROR status; None otherwise

Design notes
------------
- SKIPPED entries are silently discarded — they add no diagnostic value
  and inflate the log file unnecessarily.
- Thread-safety is NOT guaranteed — all bootstrap scripts are single-process.
- The log file is a JSON array. Each run appends to it; it is never truncated.
  To reset, delete the file manually.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional


StatusType = Literal["SUCCESS", "EMPTY", "ERROR", "SKIPPED"]


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def load_log(log_path: Path) -> list[dict]:
    """
    Load all existing log entries from ``bootstrap_log.json``.

    Parameters
    ----------
    log_path : path to the log file (typically ``local/db/bootstrap_log.json``)

    Returns
    -------
    list[dict]
        All existing entries, or an empty list if the file does not exist.
    """
    if log_path.exists():
        with open(log_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def append_log(log_path: Path, entry: dict) -> None:
    """
    Append a single log entry to ``bootstrap_log.json``.

    SKIPPED entries are silently ignored and never written.

    Parameters
    ----------
    log_path : path to the log file
    entry    : dict produced by ``make_entry()``

    Notes
    -----
    Creates the parent directory if it does not exist.
    The log file is read, the entry is appended, and the file is rewritten.
    Not safe for concurrent writes — bootstrap scripts are single-process.
    """
    if entry.get("status") == "SKIPPED":
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entries = load_log(log_path)
    entries.append(entry)
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def make_entry(
    source_id: str,
    period: str,
    status: StatusType,
    records: int = 0,
    bytes_written: int = 0,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
    error_message: Optional[str] = None,
    **extra_fields: Any,
) -> dict:
    """
    Build a structured log entry dict.

    Parameters
    ----------
    source_id     : data source identifier (e.g. ``'compras_gov'``)
    period        : ``'YYYY-MM'`` for monthly sources, ``'static'`` for
                    full-reload sources (e.g. transparencia)
    status        : ``SUCCESS`` | ``EMPTY`` | ``ERROR`` | ``SKIPPED``
    records       : number of records successfully written
    bytes_written : output file size in bytes (0 for EMPTY/ERROR)
    started_at    : ISO 8601 UTC string; auto-generated if None
    finished_at   : ISO 8601 UTC string; auto-generated if None
    error_message : error detail string; None for non-ERROR entries
    **extra_fields : additional fields merged into the entry dict.
                     Used by pipeline notebooks (Bronze, Silver, Gold) to
                     add layer-specific metadata without breaking the base
                     contract used by bootstrap scripts.

                     Common extra fields for Bronze notebooks:
                       batch_id         (str)  — UUID for this pipeline run
                       layer            (str)  — e.g. ``'bronze'``
                       object           (str)  — e.g. ``'compras_contratos'``
                       files            (int)  — number of Parquet files written
                       has_rescued_data (bool) — True if schema drift detected
                       drift_months     (int)  — number of months with drift

    Returns
    -------
    dict
        Ready to pass to ``append_log()``.
        Extra fields are appended after the standard fields.

    Examples
    --------
    >>> # Bootstrap script usage (unchanged)
    >>> entry = make_entry("compras_gov", "2026-04", "SUCCESS",
    ...                    records=12_500, bytes_written=4_194_304)
    >>> append_log(LOG_PATH, entry)

    >>> # Bronze notebook usage (with extra fields)
    >>> entry = make_entry(
    ...     "compras_gov", "2021-01/2026-04", "SUCCESS",
    ...     records=761_370, bytes_written=149_422_080,
    ...     batch_id=BATCH_ID, layer="bronze",
    ...     object="compras_contratos", files=64,
    ...     has_rescued_data=False, drift_months=0,
    ... )
    >>> append_log(LOG_PATH, entry)
    """
    now = datetime.now(timezone.utc).isoformat()
    entry = {
        "source_id"    : source_id,
        "period"       : period,
        "status"       : status,
        "records"      : records,
        "bytes_written": bytes_written,
        "started_at"   : started_at if started_at is not None else now,
        "finished_at"  : finished_at if finished_at is not None else now,
        "error_message": error_message,
    }
    # Merge extra fields after standard fields — order is preserved (Python 3.7+)
    entry.update(extra_fields)
    return entry


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def get_last_entry(log_path: Path, source_id: str) -> Optional[dict]:
    """
    Return the most recent log entry for a given source.

    Parameters
    ----------
    log_path  : path to the log file
    source_id : source identifier to filter by

    Returns
    -------
    dict or None
        Most recent entry (by ``finished_at``), or None if no entries found.
    """
    entries = [e for e in load_log(log_path) if e.get("source_id") == source_id]
    if not entries:
        return None
    return sorted(entries, key=lambda e: e.get("finished_at", ""), reverse=True)[0]


def get_successful_periods(log_path: Path, source_id: str) -> set[str]:
    """
    Return the set of periods already successfully downloaded for a source.

    Used by bootstrap scripts for idempotency checks: if a period is in
    this set, it can be skipped.

    Parameters
    ----------
    log_path  : path to the log file
    source_id : source identifier to filter by

    Returns
    -------
    set[str]
        Periods (``'YYYY-MM'`` strings) with ``status == 'SUCCESS'``.
    """
    return {
        e["period"]
        for e in load_log(log_path)
        if e.get("source_id") == source_id and e.get("status") == "SUCCESS"
    }
