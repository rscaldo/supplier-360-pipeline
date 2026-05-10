"""
03_bootstrap_transparencia.py
------------------------------
Downloads sanction data from Portal da Transparência (CEIS and CNEP).

Full reload by design: both datasets are small (~82 MB total) and represent
the complete historical base. No incremental logic needed — every run
overwrites the previous files with the latest data.

Logs each execution to local/db/bootstrap_log.json via utils/bootstrap_log.py.

Usage
-----
    python 03_bootstrap_transparencia.py

Environment variables (via local/.env)
---------------------------------------
    TRANSPARENCIA_API_KEY : API key from Portal da Transparência (required)
                            Obtain/renew at: https://portaldatransparencia.gov.br/

Partial download policy (Option A)
------------------------------------
    If any page fails after MAX_RETRIES, no file is written for that endpoint.
    The next run re-downloads from page 1. This guarantees that any file on
    disk is always complete — no ambiguous partial state.

Pipeline position
-----------------
    01 bootstrap_receita_federal
    02 bootstrap_pncp
    03 bootstrap_transparencia    <- this script
    04 bootstrap_compras
    05 ... bronze notebooks
"""

import json
import os
import ctypes
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
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

API_KEY = os.getenv("TRANSPARENCIA_API_KEY")

SOURCE_ID  = "portal_transparencia"
OUTPUT_DIR = PROJECT_ROOT / "data" / "raw" / "portal_transparencia"
LOG_PATH   = PROJECT_ROOT / "db" / "bootstrap_log.json"

BASE_URL     = "https://api.portaldatransparencia.gov.br/api-de-dados"
PAGE_DELAY   = 0.05    # seconds between pages — be a good API citizen
TIMEOUT      = 30      # seconds per request

# Retry configuration with exponential backoff
MAX_RETRIES  = 3
RETRY_DELAY  = 5       # base delay in seconds
BACKOFF_MULT = 2.0     # multiplier per attempt (5s, 10s, 20s)

# Endpoints — full historical base, no date filter
# period = endpoint name ('ceis' | 'cnep') — used as log key
ENDPOINTS = {
    "ceis": OUTPUT_DIR / "ceis.json",
    "cnep": OUTPUT_DIR / "cnep.json",
}


# ---------------------------------------------------------------------------
# Core download function
# ---------------------------------------------------------------------------

def fetch_all_pages(endpoint: str, output_path: Path, headers: dict) -> dict:
    """
    Download all pages from a Portal da Transparência endpoint.

    Full reload: overwrites any existing file on every run.

    Retry policy: up to MAX_RETRIES attempts per page with exponential backoff.
    Delays: RETRY_DELAY x BACKOFF_MULT^(attempt-1) — 5s, 10s, 20s.

    Partial download policy (Option A): if any page fails after all retries,
    no file is written and ERROR is returned. The next run re-downloads from
    page 1. This guarantees no ambiguous partial state on disk.

    Parameters
    ----------
    endpoint    : API endpoint name — 'ceis' or 'cnep'
    output_path : local path to write the JSON file
    headers     : request headers including API key

    Returns
    -------
    dict : log entry (SUCCESS | EMPTY | ERROR)
    """
    period     = endpoint   # period key for this full-reload source
    started_at = datetime.now(timezone.utc).isoformat()

    print(f"\n[{endpoint.upper()}] Starting full reload...")

    registros = []
    pagina    = 1

    while True:
        attempt = 0
        success = False
        data    = None

        while attempt < MAX_RETRIES and not success:
            try:
                resp = requests.get(
                    f"{BASE_URL}/{endpoint}",
                    headers=headers,
                    params={"pagina": pagina},
                    timeout=TIMEOUT,
                )

                # Empty body = past the last page — download is complete
                if not resp.text.strip():
                    success = True
                    data    = []
                    break

                if resp.status_code != 200:
                    raise ValueError(f"HTTP {resp.status_code}: {resp.text[:200]}")

                data = resp.json()

                # Empty list = past the last page — download is complete
                if not data:
                    success = True
                    break

                registros.extend(data)
                print(f"  page {pagina} | accumulated: {len(registros):,} records")
                success = True

            except Exception as exc:  # noqa: BLE001
                attempt += 1
                delay = RETRY_DELAY * (BACKOFF_MULT ** (attempt - 1))
                print(
                    f"  [WARN] page {pagina}"
                    f" attempt {attempt}/{MAX_RETRIES} failed: {exc}"
                    f" — retrying in {delay:.0f}s"
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
            return make_entry(
                source_id=SOURCE_ID, period=period, status="ERROR",
                error_message=msg, started_at=started_at, finished_at=finished_at,
            )

        # Empty data signals end of pagination — exit loop
        if data is not None and not data:
            break

        time.sleep(PAGE_DELAY)
        pagina += 1

    finished_at = datetime.now(timezone.utc).isoformat()

    # Guard: do not write empty files
    if not registros:
        print(f"  [WARN] No records for {endpoint} — skipping file write")
        return make_entry(
            source_id=SOURCE_ID, period=period, status="EMPTY",
            started_at=started_at, finished_at=finished_at,
        )

    # Write JSON — only reached on complete successful download
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(registros, f, ensure_ascii=False, indent=2)

    bytes_written = output_path.stat().st_size
    print(
        f"  Done — {len(registros):,} records"
        f" | {bytes_written / (1024 * 1024):.1f} MB"
        f" -> {output_path.name}"
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
        # Secret validation — fail explicitly with clear message
        if not API_KEY:
            print("[ERROR] TRANSPARENCIA_API_KEY is not set.")
            print("        Add it to local/.env:")
            print("          TRANSPARENCIA_API_KEY=<your_key>")
            print("        Obtain/renew at: https://portaldatransparencia.gov.br/")
            sys.exit(1)

        headers = {"chave-api-dados": API_KEY}

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        print(f"Source      : {SOURCE_ID}")
        print(f"Output dir  : {OUTPUT_DIR}")
        print(f"Log path    : {LOG_PATH}")
        print(f"Endpoints   : {', '.join(ENDPOINTS.keys())}")
        print(f"Strategy    : full reload (overwrites existing files)")
        print(f"Retries     : {MAX_RETRIES} (backoff {RETRY_DELAY}s x {BACKOFF_MULT}x)")

        all_ok = True

        for endpoint, output_path in ENDPOINTS.items():
            entry = fetch_all_pages(endpoint, output_path, headers)
            append_log(LOG_PATH, entry)

            if entry["status"] != "SUCCESS":
                all_ok = False
                print(f"  [WARN] {endpoint} completed with status: {entry['status']}")

        print(f"\n{'=' * 45}")
        print(f"Run complete — {'all endpoints OK' if all_ok else 'some endpoints failed'}")
        print(f"Log : {LOG_PATH}")


    finally:
        # Always restore normal sleep — even if an exception occurs
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        print("Sleep prevention restored.")

if __name__ == "__main__":
    main()
