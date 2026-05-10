"""
paths.py — Project path resolution and DuckDB-safe path formatting.

Handles cross-platform path resolution for Windows and Linux/macOS,
and converts paths to forward-slash strings safe for DuckDB SQL.

All public functions return strings or Path objects — never raw os.sep
strings. Callers should not need to call .replace() manually.
"""
from __future__ import annotations

import builtins
import inspect
from pathlib import Path


# ---------------------------------------------------------------------------
# Project root resolution
# ---------------------------------------------------------------------------

def get_project_root() -> Path:
    """
    Resolve PROJECT_ROOT regardless of execution context.

    Candidate order:
      1. VS Code Jupyter kernel path, when available.
      2. Current working directory.
      3. Caller files from the Python stack.

    Implementation note: every candidate is validated by walking upward until
    repo markers are found, so internal .venv/Jupyter frames are ignored.

    Returns
    -------
    Path
        Absolute path to the project root (the ``local/`` directory).

    Examples
    --------
    >>> root = get_project_root()
    >>> (root / "data" / "silver").exists()
    True
    """
    def find_root(start: Path) -> Path | None:
        """Walk upward from ``start`` until repo/project markers are found."""
        current = start if start.is_dir() else start.parent
        for candidate in [current, *current.parents]:
            has_utils = (candidate / "utils" / "paths.py").exists()
            has_notebooks = (candidate / "notebooks").is_dir()
            has_data_or_db = (candidate / "data").is_dir() or (candidate / "db").is_dir()
            has_git = (candidate / ".git").exists()
            if has_utils and has_notebooks and (has_data_or_db or has_git):
                return candidate
        return None

    candidates: list[Path] = []

    # VS Code Jupyter injects __vsc_ipynb_file__ as a builtin.
    vsc_file = getattr(builtins, "__vsc_ipynb_file__", None)
    if vsc_file:
        candidates.append(Path(vsc_file))

    # Prefer the launch directory before inspecting stack frames.
    candidates.append(Path.cwd())

    # Walk the call stack, but validate each candidate against repo markers.
    for frame in inspect.stack():
        filename = frame.filename
        if filename and filename != "<string>" and not filename.startswith("<"):
            candidate = Path(filename)
            if candidate.suffix in (".py", ".ipynb"):
                candidates.append(candidate)

    for candidate in candidates:
        try:
            root = find_root(candidate.resolve())
        except OSError:
            continue
        if root is not None:
            return root

    # Last resort
    return Path.cwd().resolve().parent


# ---------------------------------------------------------------------------
# SQL-safe path formatting
# ---------------------------------------------------------------------------

def to_sql_path(path: Path | str) -> str:
    """
    Convert a Path to a forward-slash string safe for DuckDB SQL.

    DuckDB's SQL parser requires forward slashes even on Windows.
    Always call this before passing any path into a SQL string.

    Parameters
    ----------
    path : Path or str

    Returns
    -------
    str
        Path string with all backslashes replaced by forward slashes.

    Examples
    --------
    >>> to_sql_path(Path(r"C:\\data\\silver\\silver_ceis\\data.parquet"))
    'C:/data/silver/silver_ceis/data.parquet'
    """
    return str(path).replace("\\", "/")


# ---------------------------------------------------------------------------
# Layer-specific path helpers
# ---------------------------------------------------------------------------

def raw_path(root: Path, source: str) -> Path:
    """
    Return the raw data directory for a bootstrap source.

    Parameters
    ----------
    root   : PROJECT_ROOT (Path)
    source : source name, e.g. ``'pncp'``, ``'compras_gov'``

    Returns
    -------
    Path (directory — caller appends filename)
    """
    return root / "data" / "raw" / source


def bronze_path(root: Path, table: str, glob: bool = False) -> str:
    """
    Return the SQL-safe path for a bronze table.

    Parameters
    ----------
    root  : PROJECT_ROOT (Path)
    table : table directory name, e.g. ``'bronze_compras'``
    glob  : if True, appends ``/**/*.parquet`` for partitioned tables

    Returns
    -------
    str — forward-slash path for use in DuckDB SQL
    """
    base = root / "data" / "bronze" / table
    if glob:
        return to_sql_path(base / "**" / "*.parquet")
    return to_sql_path(base / "data.parquet")


def silver_path(root: Path, table: str, glob: bool = False) -> str:
    """
    Return the SQL-safe path for a silver table.

    Parameters
    ----------
    root  : PROJECT_ROOT (Path)
    table : table directory name, e.g. ``'silver_ceis'``
    glob  : if True, appends ``/**/*.parquet`` for hive-partitioned tables
            (e.g. silver_compras partitioned by _year_month)

    Returns
    -------
    str — forward-slash path for use in DuckDB SQL

    Examples
    --------
    >>> silver_path(root, "silver_ceis")
    'C:/project/local/data/silver/silver_ceis/data.parquet'

    >>> silver_path(root, "silver_compras", glob=True)
    'C:/project/local/data/silver/silver_compras/**/*.parquet'
    """
    base = root / "data" / "silver" / table
    if glob:
        return to_sql_path(base / "**" / "*.parquet")
    return to_sql_path(base / "data.parquet")


def gold_path(root: Path, table: str) -> str:
    """
    Return the SQL-safe path for a gold parquet file.

    Parameters
    ----------
    root  : PROJECT_ROOT (Path)
    table : table name without extension, e.g. ``'dim_fornecedor'``

    Returns
    -------
    str — forward-slash path for use in DuckDB SQL
    """
    return to_sql_path(root / "data" / "gold" / f"{table}.parquet")


def serving_path(root: Path, table: str) -> str:
    """
    Return the SQL-safe path for a serving layer parquet file.

    Parameters
    ----------
    root  : PROJECT_ROOT (Path)
    table : table name without extension, e.g. ``'serving_fornecedor_perfil'``

    Returns
    -------
    str — forward-slash path for use in DuckDB SQL
    """
    return to_sql_path(root / "data" / "serving" / f"{table}.parquet")


def ensure_dir(path: Path) -> Path:
    """
    Create a directory (and parents) if it does not exist.

    Parameters
    ----------
    path : directory path to create

    Returns
    -------
    Path — the same path, for chaining
    """
    path.mkdir(parents=True, exist_ok=True)
    return path
