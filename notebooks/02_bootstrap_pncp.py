"""
02_bootstrap_pncp.py
--------------------
Downloads monthly contract data from PNCP (Portal Nacional de Contratações Públicas).
Covers 2021-01 through the current month (national scope — no UF filter).

Idempotent: skips months already downloaded.
Logs every execution to local/db/bootstrap_log.json via utils/bootstrap_log.py.

Usage
-----
    python 02_bootstrap_pncp.py

Environment variables (via local/.env)
---------------------------------------
    None required — API is public, no authentication needed.

API notes
---------
    Endpoint : https://pncp.gov.br/api/consulta/v1/contratos
    Params   : dataInicial (YYYYMMDD), dataFinal (YYYYMMDD), pagina, tamanhoPagina
    Max page : 500 records (above this returns HTTP 400)
    No UF filter — API ignores uf= param; national data returned by default.
    The filter that existed in silver_pncp (unidadeOrgao.ufSigla = 'SP') is
    applied at the Silver layer — raw data here is always national scope.

    IMPORTANT (P27): API was returning timeouts for pre-2026 data on 2026-04-28.
    START_DATE is set to 2021-01-01 (correct historical scope). If timeouts persist,
    run with a recent month to test connectivity before running the full history.

Partial download policy (Option A)
------------------------------------
    If a page fails after MAX_RETRIES, all partial records for that month are
    discarded and the output file is deleted. The next run re-downloads from
    page 1. This guarantees that any file on disk is always complete — no
    ambiguous partial state.

Pipeline position
-----------------
    01 bootstrap_receita_federal
    02 bootstrap_pncp             <- this script
    03 bootstrap_transparencia
    04 bootstrap_compras
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

SOURCE_ID  = "pncp"
OUTPUT_DIR = PROJECT_ROOT / "data" / "raw" / "pncp"
LOG_PATH   = PROJECT_ROOT / "db" / "bootstrap_log.json"

BASE_URL   = "https://pncp.gov.br/api/consulta/v1/contratos"
PAGE_SIZE  = 500    # max accepted by PNCP API
TIMEOUT    = 60     # seconds per request — increased from 10 (API can be slow)
PAGE_DELAY = 2      # seconds between pages — be a good API citizen

# Retry configuration with exponential backoff
MAX_RETRIES  = 15
RETRY_DELAY  = 3     # base delay in seconds
BACKOFF_MULT = 1.5   # multiplier per attempt (3s, 4.5s, 6.75s, 10.1s, ...)

# Historical scope — national, no UF filter
# P27: API was unstable on 2026-04-28 for pre-2026 dates.
# START_DATE is correct — test with a recent month first if API issues persist.
START_DATE = date(2021, 1, 1)
END_DATE   = date.today().replace(day=1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def generate_months(start: date, end: date) -> list[tuple[str, str, str]]:
    """
    Generate (period, date_ini, date_fin) tuples between start and end (inclusive).

    Parameters
    ----------
    start : first month (day ignored — always uses day=1)
    end   : last month  (day ignored — always uses last day of month)

    Returns
    -------
    list of (period, date_ini, date_fin) where:
        period   = 'YYYY-MM'
        date_ini = 'YYYYMMDD' (first day of month)
        date_fin = 'YYYYMMDD' (last day of month)
    """
    months  = []
    current = start.replace(day=1)
    while current <= end:
        last_day = current + relativedelta(months=1) - relativedelta(days=1)
        months.append((
            current.strftime("%Y-%m"),
            current.strftime("%Y%m%d"),
            last_day.strftime("%Y%m%d"),
        ))
        current += relativedelta(months=1)
    return months


# ---------------------------------------------------------------------------
# Core download function
# ---------------------------------------------------------------------------

def fetch_month(period: str, date_ini: str, date_fin: str) -> dict:
    """
    Download all contracts for a given month and write to a JSON file.

    Idempotency: skips the file if it already exists on disk.
    Note: unlike RF bootstrap, PNCP files are JSON (not ZIP) — no
    integrity check beyond file existence is needed.

    Retry policy: up to MAX_RETRIES attempts per page with exponential backoff.
    Delays: RETRY_DELAY x BACKOFF_MULT^(attempt-1) — 3s, 4.5s, 6.75s, ...

    Partial download policy (Option A): if any page fails after all retries,
    partial records are discarded and the output file is deleted. The next run
    will re-download from page 1. This guarantees no ambiguous partial state.

    Parameters
    ----------
    period   : 'YYYY-MM' — used as output filename
    date_ini : 'YYYYMMDD' start of month
    date_fin : 'YYYYMMDD' end of month

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

    print(f"[{period}] Downloading {date_ini} -> {date_fin}")
    start_time = time.time()
    registros  = []
    pagina     = 1

    while True:
        attempt = 0
        success = False
        data    = {}

        while attempt < MAX_RETRIES and not success:
            try:
                resp = requests.get(
                    BASE_URL,
                    params={
                        "dataInicial"  : date_ini,
                        "dataFinal"    : date_fin,
                        "pagina"       : pagina,
                        "tamanhoPagina": PAGE_SIZE,
                    },
                    timeout=TIMEOUT,
                )

                # Guard: empty body — PNCP returns empty string on some errors
                if not resp.text.strip():
                    raise ValueError("Empty response body")

                if resp.status_code != 200:
                    raise ValueError(f"HTTP {resp.status_code}")

                data              = resp.json()
                paginas_restantes = data.get("paginasRestantes", 0)
                total_paginas     = data.get("totalPaginas", 0)

                registros.extend(data.get("data", []))
                print(
                    f"  page {pagina}/{total_paginas}"
                    f" | records so far: {len(registros)}"
                )
                success = True

                if paginas_restantes == 0:
                    break

                pagina += 1
                time.sleep(PAGE_DELAY)

            except Exception as exc:  # noqa: BLE001
                attempt += 1
                delay = RETRY_DELAY * (BACKOFF_MULT ** (attempt - 1))
                print(
                    f"  [WARN] page {pagina}"
                    f" attempt {attempt}/{MAX_RETRIES} failed: {exc}"
                    f" — retrying in {delay:.1f}s"
                )
                time.sleep(delay)

        if not success:
            # Option A: discard all partial results — guarantees complete files on disk.
            # Next run will re-download from page 1.
            finished_at = datetime.now(timezone.utc).isoformat()
            msg = f"Page {pagina} failed after {MAX_RETRIES} retries"
            print(
                f"  [ERROR] {msg}"
                f" — discarding {len(registros):,} partial records"
                f" — full re-download on next run"
            )
            filepath.unlink(missing_ok=True)
            return make_entry(
                source_id=SOURCE_ID, period=period, status="ERROR",
                error_message=msg, started_at=started_at, finished_at=finished_at,
            )

        if data.get("paginasRestantes", 0) == 0:
            break

    # Guard: do not write empty files (API can return 0 records for some months)
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
    duration      = int(time.time() - start_time)
    finished_at   = datetime.now(timezone.utc).isoformat()

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

        for period, date_ini, date_fin in months:
            entry = fetch_month(period, date_ini, date_fin)
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
