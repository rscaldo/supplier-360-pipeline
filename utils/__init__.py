"""
utils — Fornecedor 360 Público shared utilities.

Modules
-------
paths         Project path resolution and DuckDB-safe path formatting.
duckdb_utils  DuckDB connection factory and query helpers.
validation    CheckSuite — PASS/FAIL framework for notebook validation.
privacy       CNPJTokeniser — HMAC-SHA256 pseudonymisation (ADR-005).
bootstrap_log Structured logging shared across all bootstrap scripts.
pipeline      Pipeline coordination — landing gate check for Bronze notebooks.

Usage
-----
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path.cwd().parent / "utils"))

    from paths import get_project_root, to_sql_path, silver_path, gold_path
    from duckdb_utils import get_connection, register_parquet_view, scalar
    from validation import CheckSuite
    from privacy import CNPJTokeniser
    from bootstrap_log import load_log, append_log, make_entry
    from pipeline import check_landing_gate
"""
