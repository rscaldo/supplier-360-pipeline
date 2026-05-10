"""
01_bootstrap_receita_federal.py
--------------------------------
Downloads monthly CNPJ data published by Receita Federal via Nextcloud (SERPRO).

Downloads one month at a time — specify month as argument or defaults to current month.
Idempotent: skips files already downloaded and verified (file-level granularity, ~4.5 GB/month).
Logs each execution to local/db/bootstrap_log.json via utils/bootstrap_log.py.

Usage
-----
    python 01_bootstrap_receita_federal.py            # current month
    python 01_bootstrap_receita_federal.py 2026-03    # specific month

Environment variables (via local/.env)
---------------------------------------
    RECEITA_FEDERAL_SHARE_TOKEN : Nextcloud share token (required)

About the share token
---------------------
    The token is the last segment of the Nextcloud share URL:
        https://arquivos.receitafederal.gov.br/index.php/s/XXXXXXXXXXXXXXXXXXXXX
                                                                  ^^^^^^^^^^^^^^^^
    SERPRO may rotate this token — verify monthly before running:
        1. Visit https://arquivos.receitafederal.gov.br
        2. Navigate to Dados > Cadastros > CNPJ
        3. Check that the current month folder exists
        4. If URL changed, extract new token and update .env

Files downloaded (~4.5 GB/month)
----------------------------------
    - Empresas0.zip to Empresas9.zip
    - Estabelecimentos0.zip to Estabelecimentos9.zip
    - Simples.zip, Cnaes.zip, Municipios.zip,
      Naturezas.zip, Qualificacoes.zip, Paises.zip, Motivos.zip

Intentionally excluded (ADR-005 — Privacy by Design)
------------------------------------------------------
    - Socios0.zip to Socios9.zip
      Reason: contain partial CPF — personal data under LGPD.

Pipeline position
-----------------
    01 bootstrap_receita_federal  ← this script
    02 bootstrap_pncp
    03 bootstrap_transparencia
    04 bootstrap_compras
    05 ... bronze notebooks
"""

import os
import ctypes
import sys
import time
import zipfile as zf
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

SHARE_TOKEN = os.getenv("RECEITA_FEDERAL_SHARE_TOKEN")

SOURCE_ID   = "receita_federal"
CHUNK_SIZE  = 8 * 1024 * 1024   # 8 MB streaming chunks
LOG_PATH    = PROJECT_ROOT / "db" / "bootstrap_log.json"

# Retry configuration for download_file
MAX_RETRIES  = 3
RETRY_DELAY  = 5    # seconds — base delay between retries
BACKOFF_MULT = 2.0  # exponential backoff multiplier

ARQUIVOS = (
    [f"Empresas{i}.zip"        for i in range(10)] +
    [f"Estabelecimentos{i}.zip" for i in range(10)] +
    [
        "Simples.zip", "Cnaes.zip", "Municipios.zip",
        "Naturezas.zip", "Qualificacoes.zip", "Paises.zip", "Motivos.zip",
    ]
)


# ---------------------------------------------------------------------------
# Zip verification
# ---------------------------------------------------------------------------

def verify_zip(path: Path) -> bool:
    """
    Verify zip file integrity using zipfile.testzip().

    More reliable than checking file size alone — detects partial downloads
    and internal corruption that size checks cannot catch.

    Parameters
    ----------
    path : path to the zip file

    Returns
    -------
    bool
        True if valid. False if corrupted — the file is deleted automatically.
    """
    try:
        with zf.ZipFile(path, "r") as z:
            bad_file = z.testzip()
        if bad_file is not None:
            print(f"  [CORRUPT]  {path.name} — first bad file: {bad_file}")
            path.unlink(missing_ok=True)
            return False
        return True
    except zf.BadZipFile as exc:
        print(f"  [CORRUPT]  {path.name} — {exc}")
        path.unlink(missing_ok=True)
        return False


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

def check_availability(base_url: str) -> bool:
    """
    Check whether data for the requested month is available on Nextcloud.

    Uses Cnaes.zip as a proxy — it is the smallest file and fastest to check.

    Parameters
    ----------
    base_url : WebDAV base URL for the month

    Returns
    -------
    bool
    """
    try:
        resp = requests.head(
            f"{base_url}/Cnaes.zip",
            auth=(SHARE_TOKEN, ""),
            timeout=30,
        )
        return resp.status_code == 200
    except requests.RequestException as exc:
        print(f"  [WARN] Availability check failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# Download with retry and backoff
# ---------------------------------------------------------------------------

def download_file(nome: str, base_url: str, destino: Path) -> tuple[bool, int]:
    """
    Download a single ZIP file from Nextcloud via public WebDAV.

    Idempotency: skips the file if it already exists AND passes zip integrity check.

    Retry policy: up to MAX_RETRIES attempts with exponential backoff.
    Backoff delays: 5s, 10s, 20s (base × multiplier^attempt).

    Parameters
    ----------
    nome     : filename (e.g. ``'Empresas0.zip'``)
    base_url : WebDAV base URL for the month
    destino  : local directory to write the file into

    Returns
    -------
    tuple[bool, int]
        (success, bytes_written)
    """
    local_path = destino / nome

    # Idempotency check — skip if already downloaded and valid
    if local_path.exists():
        if verify_zip(local_path):
            print(f"  [SKIPPED]  {nome} — already exists and verified")
            return True, local_path.stat().st_size
        else:
            print(f"  [RETRY]    {nome} — was corrupt, re-downloading")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            print(f"  [DOWNLOAD] {nome}  (attempt {attempt}/{MAX_RETRIES})")

            resp = requests.get(
                f"{base_url}/{nome}",
                auth=(SHARE_TOKEN, ""),
                stream=True,
                timeout=120,
            )

            if resp.status_code != 200:
                raise ValueError(f"HTTP {resp.status_code}")

            total_mb      = int(resp.headers.get("Content-Length", 0)) / (1024 * 1024)
            bytes_written = 0

            with open(local_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:
                        f.write(chunk)
                        bytes_written += len(chunk)
                        print(
                            f"    {bytes_written / (1024 * 1024):.1f} MB"
                            f" / {total_mb:.1f} MB",
                            end="\r",
                        )

            print(f"\n  [OK]       {nome} — {bytes_written / (1024 * 1024):.1f} MB")

            # Verify integrity before accepting the download
            if not verify_zip(local_path):
                raise ValueError("downloaded file failed zip verification")

            return True, bytes_written

        except Exception as exc:  # noqa: BLE001
            print(f"  [WARN]     attempt {attempt}/{MAX_RETRIES} failed: {exc}")
            # Clean up partial file
            local_path.unlink(missing_ok=True)

            if attempt < MAX_RETRIES:
                delay = RETRY_DELAY * (BACKOFF_MULT ** (attempt - 1))
                print(f"  [RETRY]    waiting {delay:.0f}s before next attempt...")
                time.sleep(delay)

    print(f"  [ERROR]    {nome} — all {MAX_RETRIES} attempts failed")
    return False, 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # Prevent Windows from sleeping while bootstrap is running.
    # ES_CONTINUOUS (0x80000000) | ES_SYSTEM_REQUIRED (0x00000001)
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)

    try:
        # Secret validation — fail explicitly with clear message
        if not SHARE_TOKEN:
            print("[ERROR] RECEITA_FEDERAL_SHARE_TOKEN is not set.")
            print("        Add it to local/.env:")
            print("          RECEITA_FEDERAL_SHARE_TOKEN=<token>")
            print("        Find the token at https://arquivos.receitafederal.gov.br")
            sys.exit(1)

        # Resolve target month from argument or current month
        mes = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m")

        base_url = (
            f"https://arquivos.receitafederal.gov.br"
            f"/public.php/webdav/Dados/Cadastros/CNPJ/{mes}"
        )
        destino = PROJECT_ROOT / "data" / "raw" / "receita_federal" / mes

        print(f"Source      : {SOURCE_ID}")
        print(f"Month       : {mes}")
        print(f"Destination : {destino}")
        print(f"Log path    : {LOG_PATH}")
        print(f"Files       : {len(ARQUIVOS)}")
        print(f"Retries     : {MAX_RETRIES} (backoff {RETRY_DELAY}s × {BACKOFF_MULT}x)\n")

        started_at = datetime.now(timezone.utc).isoformat()

        # Full idempotency check — all files exist and pass zip verification
        if destino.exists():
            existing = [f for f in ARQUIVOS if (destino / f).exists()]
            if len(existing) == len(ARQUIVOS):
                print("Verifying existing files...")
                all_valid = all(verify_zip(destino / f) for f in ARQUIVOS)
                if all_valid:
                    print(f"All {len(ARQUIVOS)} files already exist and verified — skipping")
                    append_log(LOG_PATH, make_entry(
                        source_id=SOURCE_ID,
                        period=mes,
                        status="SKIPPED",
                        started_at=started_at,
                        finished_at=datetime.now(timezone.utc).isoformat(),
                    ))
                    return
                else:
                    print("[WARN] Some files are corrupt — re-downloading affected files")

        # Availability check before creating directories or downloading anything
        print(f"Checking availability for {mes} on Nextcloud...")
        if not check_availability(base_url):
            print(f"[WARN] Data for {mes} is not yet available.")
            print("       Receita Federal usually publishes between the 8th and 12th of the month.")
            sys.exit(1)

        print(f"Data for {mes} is available. Starting download.\n")
        destino.mkdir(parents=True, exist_ok=True)

        total_bytes  = 0
        failed_files = []

        for i, arquivo in enumerate(ARQUIVOS, start=1):
            print(f"\n--- File {i}/{len(ARQUIVOS)} ---")
            success, bytes_written = download_file(arquivo, base_url, destino)
            if success:
                total_bytes += bytes_written
            else:
                failed_files.append(arquivo)

        finished_at = datetime.now(timezone.utc).isoformat()

        if failed_files:
            error_msg = f"Failed files: {', '.join(failed_files)}"
            print(f"\n[ERROR] {error_msg}")
            append_log(LOG_PATH, make_entry(
                source_id=SOURCE_ID,
                period=mes,
                status="ERROR",
                bytes_written=total_bytes,
                started_at=started_at,
                finished_at=finished_at,
                error_message=error_msg,
            ))
        else:
            size_gb = total_bytes / (1024 ** 3)
            print(f"\nDone — {size_gb:.2f} GB downloaded for {mes}")
            append_log(LOG_PATH, make_entry(
                source_id=SOURCE_ID,
                period=mes,
                status="SUCCESS",
                bytes_written=total_bytes,
                started_at=started_at,
                finished_at=finished_at,
            ))

        print(f"Log written to: {LOG_PATH}")


    finally:
        # Always restore normal sleep — even if an exception occurs
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        print("Sleep prevention restored.")

if __name__ == "__main__":
    main()
