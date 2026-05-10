"""
04_bootstrap_compras.py
------------------------
Downloads monthly contract data from Compras.gov (Módulo 09 — Contratos).
Covers 2021-01 through the current month. National scope — no UF filter.

Idempotent: skips months already downloaded.
Logs every execution to local/db/bootstrap_log.json via utils/bootstrap_log.py.

Usage
-----
    python 04_bootstrap_compras.py

Environment variables (via local/.env)
---------------------------------------
    None required — API is public, no authentication needed.

API notes
---------
    Endpoint  : https://dadosabertos.compras.gov.br/modulo-contratos/1_consultarContratos
    Filter    : dataVigenciaInicialMin / dataVigenciaInicialMax (YYYY-MM-DD)
    Page size : 500 records max
    No UF filter — national scope. Previous SP filter (ADR-004) removed.

    Coverage: 2021-01 onwards (SICON covers pre-2021 data but has no public API).
    Pre-2021 history registered as P29 for future work.

Output structure (matches PNCP pattern)
-----------------------------------------
    data/raw/compras_gov/
      2021-01.json
      2021-02.json
      ...

    One file per month — no subdirectory per month.
    Previous structure (2021-01/contratos.json) is deprecated.

Partial download policy (Option A)
------------------------------------
    If any page fails after MAX_RETRIES, all partial records are discarded
    and no file is written. The next run re-downloads from page 1.
    This guarantees that any file on disk is always complete.

Pipeline position
-----------------
    01 bootstrap_receita_federal
    02 bootstrap_pncp
    03 bootstrap_transparencia
    04 bootstrap_compras          <- this script
    05 ... bronze notebooks
"""

import json
import ctypes
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap utils path — must come before utils imports
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "utils"))

load_dotenv(PROJECT_ROOT / ".env")

from bootstrap_log import append_log, make_entry  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SOURCE_ID   = "compras_gov"
OUTPUT_DIR  = PROJECT_ROOT / "data" / "raw" / "compras_gov"
LOG_PATH    = PROJECT_ROOT / "db" / "bootstrap_log.json"

BASE_URL    = "https://dadosabertos.compras.gov.br"
ENDPOINT    = "/modulo-contratos/1_consultarContratos"
PAGE_SIZE   = 500     # max accepted by Compras.gov API
PAGE_DELAY  = 0.05    # seconds between pages — be a good API citizen
TIMEOUT     = 60      # seconds per request

# Retry configuration with exponential backoff
MAX_RETRIES  = 3
RETRY_DELAY  = 5      # base delay in seconds
BACKOFF_MULT = 2.0    # multiplier per attempt (5s, 10s, 20s)

# Historical scope — national, no UF filter (P29: pre-2021 data in SICON, no public API)
START_DATE = date(2021, 1, 1)
END_DATE   = date.today().replace(day=1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def generate_months(start: date, end: date) -> list[tuple[str, str, str]]:
    """
    Generate (period, date_min, date_max) tuples between start and end (inclusive).

    Returns
    -------
    list of (period, date_min, date_max) where:
        period   = 'YYYY-MM'
        date_min = 'YYYY-MM-DD' (first day of month)
        date_max = 'YYYY-MM-DD' (last day of month)
    """
    months  = []
    current = start.replace(day=1)
    while current <= end:
        last_day = current + relativedelta(months=1) - relativedelta(days=1)
        months.append((
            current.strftime("%Y-%m"),
            current.strftime("%Y-%m-%d"),
            last_day.strftime("%Y-%m-%d"),
        ))
        current += relativedelta(months=1)
    return months


# ---------------------------------------------------------------------------
# HTTP fetch with retry and backoff
# ---------------------------------------------------------------------------

def fetch_page(
    date_min: str,
    date_max: str,
    pagina: int,
) -> dict | None:
    """
    Fetch a single page from the Compras.gov API.

    Retry policy: up to MAX_RETRIES attempts with exponential backoff.
    Delays: RETRY_DELAY x BACKOFF_MULT^(attempt-1) — 5s, 10s, 20s.

    Parameters
    ----------
    date_min : 'YYYY-MM-DD' start of month
    date_max : 'YYYY-MM-DD' end of month
    pagina   : page number (1-based)

    Returns
    -------
    dict : parsed JSON response, or None if empty body or all retries failed.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(
                BASE_URL + ENDPOINT,
                params={
                    "pagina"                : pagina,
                    "tamanhoPagina"         : PAGE_SIZE,
                    "dataVigenciaInicialMin": date_min,
                    "dataVigenciaInicialMax": date_max,
                },
                timeout=TIMEOUT,
            )

            if resp.status_code == 200:
                # Empty body = past the last page
                if not resp.text.strip():
                    return None
                return resp.json()

            print(
                f"  [WARN] page {pagina}"
                f" — HTTP {resp.status_code}"
                f" (attempt {attempt}/{MAX_RETRIES})"
            )

        except requests.RequestException as exc:
            print(
                f"  [WARN] page {pagina}"
                f" — {exc}"
                f" (attempt {attempt}/{MAX_RETRIES})"
            )

        if attempt < MAX_RETRIES:
            delay = RETRY_DELAY * (BACKOFF_MULT ** (attempt - 1))
            print(f"  [RETRY] waiting {delay:.0f}s...")
            time.sleep(delay)

    print(f"  [ERROR] page {pagina} — all {MAX_RETRIES} retries exhausted")
    return None


# ---------------------------------------------------------------------------
# Core download function
# ---------------------------------------------------------------------------

def fetch_month(period: str, date_min: str, date_max: str) -> dict:
    """
    Download all contracts for a given month and write to a JSON file.

    Output: data/raw/compras_gov/{period}.json (e.g. 2021-01.json)
    One file per month — no subdirectory (matches PNCP output pattern).

    Idempotency: skips if file already exists on disk.

    Partial download policy (Option A): if any page fails after all retries,
    all partial records are discarded and no file is written. The next run
    re-downloads from page 1.

    Parameters
    ----------
    period   : 'YYYY-MM' — used as output filename
    date_min : 'YYYY-MM-DD' start of month
    date_max : 'YYYY-MM-DD' end of month

    Returns
    -------
    dict : log entry (SUCCESS | SKIPPED | EMPTY | ERROR)
    """
    filepath   = OUTPUT_DIR / f"{period}.json"
    started_at = datetime.now(timezone.utc).isoformat()

    # Idempotency check — skip if already downloaded
    if filepath.exists():
        size_mb = filepath.stat().st_size / (1024 * 1024)
        print(f"[{period}] Already exists ({size_mb:.1f} MB) — skipping")
        return make_entry(
            source_id=SOURCE_ID, period=period, status="SKIPPED",
            started_at=started_at, finished_at=started_at,
        )

    print(f"[{period}] Downloading {date_min} -> {date_max}")
    start_time = time.time()
    registros  = []
    pagina     = 1

    while True:
        data = fetch_page(date_min, date_max, pagina)

        if data is None:
            if pagina == 1:
                # No data at all for this month — not an error
                finished_at = datetime.now(timezone.utc).isoformat()
                print(f"  [WARN] No data for {period} — skipping file write")
                return make_entry(
                    source_id=SOURCE_ID, period=period, status="EMPTY",
                    started_at=started_at, finished_at=finished_at,
                )
            # Option A: fetch_page returned None after retries on page > 1
            # Discard all partial results — re-download on next run
            finished_at = datetime.now(timezone.utc).isoformat()
            msg = f"Page {pagina} failed after {MAX_RETRIES} retries"
            print(
                f"  [ERROR] {msg}"
                f" — discarding {len(registros):,} partial records"
                f" — full re-download on next run"
            )
            return make_entry(
                source_id=SOURCE_ID, period=period, status="ERROR",
                error_message=msg, started_at=started_at, finished_at=finished_at,
            )

        resultado = data.get("resultado", [])

        if not resultado:
            break  # empty resultado = past last page — clean exit

        registros.extend(resultado)
        total_paginas     = data.get("totalPaginas", "?")
        paginas_restantes = data.get("paginasRestantes", 0)

        print(
            f"  page {pagina}/{total_paginas}"
            f" | records so far: {len(registros):,}"
        )

        if paginas_restantes == 0:
            break

        pagina += 1
        time.sleep(PAGE_DELAY)

    # Guard: do not write empty files
    if not registros:
        finished_at = datetime.now(timezone.utc).isoformat()
        print(f"  [WARN] No records for {period} — skipping file write")
        return make_entry(
            source_id=SOURCE_ID, period=period, status="EMPTY",
            started_at=started_at, finished_at=finished_at,
        )

    # Write JSON — only reached on complete successful download
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(registros, f, ensure_ascii=False, indent=2)

    bytes_written = filepath.stat().st_size

    # Guard: detect suspiciously small files (< 10 bytes = empty array or whitespace)
    if bytes_written < 10:
        filepath.unlink(missing_ok=True)
        finished_at = datetime.now(timezone.utc).isoformat()
        msg = f"File written but suspiciously small ({bytes_written} bytes) — deleted"
        print(f"  [ERROR] {msg}")
        return make_entry(
            source_id=SOURCE_ID, period=period, status="ERROR",
            error_message=msg, started_at=started_at, finished_at=finished_at,
        )

    duration    = int(time.time() - start_time)
    finished_at = datetime.now(timezone.utc).isoformat()

    print(
        f"  Done — {len(registros):,} records"
        f" | {bytes_written / (1024 * 1024):.1f} MB"
        f" | {duration}s"
    )
    return make_entry(
        source_id=SOURCE_ID, period=period, status="SUCCESS",
        records=len(registros), bytes_written=bytes_written,
        started_at=started_at, finished_at=finished_at,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # Prevent Windows from sleeping while bootstrap is running.
    # ES_CONTINUOUS (0x80000000) | ES_SYSTEM_REQUIRED (0x00000001)
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)

    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        months = generate_months(START_DATE, END_DATE)

        print(f"Source      : {SOURCE_ID}")
        print(f"Output dir  : {OUTPUT_DIR}")
        print(f"Log path    : {LOG_PATH}")
        print(f"Scope       : {START_DATE} -> {END_DATE} (national, no UF filter)")
        print(f"Months      : {len(months)} ({months[0][0]} -> {months[-1][0]})")
        print(f"Retries     : {MAX_RETRIES} (backoff {RETRY_DELAY}s x {BACKOFF_MULT}x)\n")

        total_records  = 0
        total_months   = 0
        skipped_months = 0
        failed_months  = []

        for period, date_min, date_max in months:
            entry = fetch_month(period, date_min, date_max)
            append_log(LOG_PATH, entry)

            if entry["status"] == "SKIPPED":
                skipped_months += 1
            elif entry["status"] == "SUCCESS":
                total_records += entry["records"]
                total_months  += 1
            elif entry["status"] in ("ERROR", "EMPTY"):
                failed_months.append(f"{period}({entry['status']})")

        print(f"\n{'=' * 45}")
        print(f"Run complete")
        print(f"Months downloaded : {total_months}")
        print(f"Months skipped    : {skipped_months}")
        print(f"Total records     : {total_records:,}")
        if failed_months:
            print(f"Failed / empty    : {', '.join(failed_months)}")
        print(f"Log               : {LOG_PATH}")


    finally:
        # Always restore normal sleep — even if an exception occurs
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        print("Sleep prevention restored.")

if __name__ == "__main__":
    main()
