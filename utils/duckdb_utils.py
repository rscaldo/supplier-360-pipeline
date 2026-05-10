"""
duckdb_utils.py — DuckDB connection factory and query helpers.

Centralises connection configuration (threads, memory, insertion order)
to ensure consistent settings across all notebooks and scripts.

Default settings are tuned for local development on a 16 GB RAM machine.
Adjust via keyword arguments when stricter limits are needed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import duckdb


# ---------------------------------------------------------------------------
# Defaults — tuned for local dev on 16 GB RAM
# ---------------------------------------------------------------------------

DEFAULT_THREADS = 4
DEFAULT_MEMORY  = "8GB"


# ---------------------------------------------------------------------------
# Connection factory
# ---------------------------------------------------------------------------

def get_connection(
    db_path: Optional[Path] = None,
    threads: int = DEFAULT_THREADS,
    memory_limit: str = DEFAULT_MEMORY,
    read_only: bool = False,
    preserve_insertion_order: bool = False,
) -> duckdb.DuckDBPyConnection:
    """
    Create and configure a DuckDB connection.

    Parameters
    ----------
    db_path : optional path to a persistent ``.duckdb`` file.
              If None, creates an in-memory connection.
    threads : number of threads (default: 4).
    memory_limit : memory cap string (default: ``'8GB'``).
    read_only : open an existing ``.duckdb`` file in read-only mode.
    preserve_insertion_order : set True only when row order must be preserved.
                               Disabling it (default) improves performance.

    Returns
    -------
    duckdb.DuckDBPyConnection

    Examples
    --------
    >>> con = get_connection()
    >>> con = get_connection(db_path=Path("local/db/bronze.duckdb"))
    >>> con = get_connection(memory_limit="4GB", threads=2)
    """
    if db_path is not None:
        con = duckdb.connect(str(db_path), read_only=read_only)
    else:
        con = duckdb.connect()

    con.execute(f"SET threads TO {threads}")
    con.execute(f"SET memory_limit = '{memory_limit}'")
    con.execute(
        "SET preserve_insertion_order = "
        + ("true" if preserve_insertion_order else "false")
    )
    return con


# ---------------------------------------------------------------------------
# View registration
# ---------------------------------------------------------------------------

def register_parquet_view(
    con: duckdb.DuckDBPyConnection,
    view_name: str,
    path: str,
    hive_partitioning: bool = False,
) -> None:
    """
    Register a Parquet file or glob pattern as a DuckDB lazy view.

    Views are lazy — no data is loaded until the view is queried.
    Use ``to_sql_path()`` from ``paths.py`` before passing ``path``.

    Parameters
    ----------
    con               : DuckDB connection
    view_name         : name of the view to create or replace
    path              : forward-slash path string or glob pattern
    hive_partitioning : True for tables partitioned by directory name
                        (e.g. ``uf=SP/``, ``_year_month=2024-01/``)

    Examples
    --------
    >>> register_parquet_view(con, "v_ceis",
    ...     silver_path(root, "silver_ceis"))
    >>> register_parquet_view(con, "v_compras",
    ...     silver_path(root, "silver_compras", glob=True),
    ...     hive_partitioning=True)
    """
    hive = ", hive_partitioning=true" if hive_partitioning else ""
    con.execute(f"""
        CREATE OR REPLACE VIEW {view_name} AS
        SELECT * FROM read_parquet('{path}'{hive})
    """)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def scalar(con: duckdb.DuckDBPyConnection, sql: str) -> Any:
    """
    Execute a query and return the first column of the first row.

    Useful for COUNT(*), MAX(), MIN() and other scalar queries.

    Parameters
    ----------
    con : DuckDB connection
    sql : SQL string that returns exactly one row and one column

    Returns
    -------
    Any — the scalar value

    Examples
    --------
    >>> n = scalar(con, "SELECT COUNT(*) FROM v_ceis")
    >>> max_date = scalar(con, "SELECT MAX(data_inicio_sancao) FROM v_ceis")
    """
    return con.execute(sql).fetchone()[0]


def table_exists(con: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    """
    Check whether a table exists in the current DuckDB connection.

    Parameters
    ----------
    con        : DuckDB connection
    table_name : table name (no schema prefix needed for in-memory)

    Returns
    -------
    bool
    """
    result = con.execute(f"""
        SELECT COUNT(*) FROM information_schema.tables
        WHERE table_name = '{table_name}'
    """).fetchone()[0]
    return result > 0


def get_schema(con: duckdb.DuckDBPyConnection, table_or_view: str) -> dict[str, str]:
    """
    Return the column names and types of a table or view.

    Parameters
    ----------
    con            : DuckDB connection
    table_or_view  : name of the table or view to inspect

    Returns
    -------
    dict[str, str] — ``{column_name: column_type}``

    Examples
    --------
    >>> schema = get_schema(con, "v_ceis")
    >>> print(schema["cnpj_normalized"])
    'VARCHAR'
    """
    rows = con.execute(f"DESCRIBE SELECT * FROM {table_or_view}").fetchall()
    return {row[0]: row[1] for row in rows}


# ---------------------------------------------------------------------------
# SQL expression generators
# ---------------------------------------------------------------------------

def cnpj_normalize_expr(field: str) -> str:
    """
    Return a SQL expression that normalises a CNPJ column.

    Removes all characters that are not digits or ASCII letters using
    DuckDB's REGEXP_REPLACE with the global flag. Safe for future
    alphanumeric CNPJs (IN RFB 2.229/2024, effective July 2026).

    Parameters
    ----------
    field : SQL column name or expression to normalise.

    Returns
    -------
    str
        SQL expression string — does not include an alias.
        Wrap in your SELECT with ``AS cnpj_normalized`` as needed.

    Examples
    --------
    >>> cnpj_normalize_expr("pessoa_cnpjFormatado")
    "REGEXP_REPLACE(pessoa_cnpjFormatado, '[^0-9A-Za-z]', '', 'g')"

    >>> # Usage inside a SQL string:
    >>> expr = cnpj_normalize_expr("pessoa_cnpjFormatado")
    >>> sql = f"SELECT {expr} AS cnpj_normalized FROM ..."
    """
    return f"REGEXP_REPLACE({field}, '[^0-9A-Za-z]', '', 'g')"


def safe_date_expr(field: str, sentinel: str, fmt: str) -> str:
    """
    Return a SQL CASE expression that maps sentinel values to NULL
    before parsing a date string with TRY_STRPTIME.

    Government data sources encode missing dates as magic strings or
    integers instead of NULL. This function centralises the pattern so
    each sentinel is handled consistently across all Silver notebooks.

    Parameters
    ----------
    field    : SQL column name to parse.
    sentinel : the sentinel value that means "no date", as a SQL literal.
               Include quotes for string sentinels.
               Examples:
                 - ``"'Sem informacao'"``  — Portal da Transparência
                 - ``"'00000000'"``        — Receita Federal (VARCHAR dates)
    fmt      : strptime format string, as a SQL literal (include quotes).
               Examples:
                 - ``"'%d/%m/%Y'"``  — Portal da Transparência
                 - ``"'%Y%m%d'"``    — Receita Federal

    Returns
    -------
    str
        Multi-line SQL CASE expression — does not include an alias.

    Notes
    -----
    Always uses TRY_STRPTIME (not STRPTIME) so unexpected values return
    NULL instead of raising an error, keeping the pipeline resilient.

    For BIGINT date columns in Receita Federal (e.g. data_situacao_cadastral),
    cast to VARCHAR first before calling this function:
        safe_date_expr("CAST(data_situacao_cadastral AS VARCHAR)", "'0'", "'%Y%m%d'")

    Examples
    --------
    >>> safe_date_expr("dataFimSancao", "'Sem informacao'", "'%d/%m/%Y'")
    '''
        CASE WHEN dataFimSancao = 'Sem informacao' OR dataFimSancao IS NULL THEN NULL
             ELSE TRY_STRPTIME(dataFimSancao, '%d/%m/%Y')::DATE
        END'''

    >>> # Receita Federal BIGINT date:
    >>> safe_date_expr("CAST(data_situacao_cadastral AS VARCHAR)", "'0'", "'%Y%m%d'")
    """
    return (
        f"\n        CASE WHEN {field} = {sentinel} OR {field} IS NULL THEN NULL"
        f"\n             ELSE TRY_STRPTIME({field}, {fmt})::DATE"
        f"\n        END"
    )