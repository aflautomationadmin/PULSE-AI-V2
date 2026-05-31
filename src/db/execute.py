from __future__ import annotations

from contextlib import closing
from decimal import Decimal
from time import perf_counter
from typing import Any

from src.config import get_settings
from src.db.connection import get_connection
from src.models import SqlExecutionResult
from src.sql_guard import ensure_safe_readonly_sql


class DatabaseExecutionError(RuntimeError):
    """Raised when SQL execution fails."""


def _round_row(row: list[Any], decimals: int = 2) -> list[Any]:
    """
    Round every float / Decimal value in a result row to ``decimals`` places.
    Non-numeric values (strings, dates, None, int) are passed through unchanged.
    Integers are intentionally left as-is — rounding 1200 to 1200.00 would
    change the type unnecessarily.
    """
    out: list[Any] = []
    for v in row:
        if isinstance(v, float):
            out.append(round(v, decimals))
        elif isinstance(v, Decimal):
            out.append(round(float(v), decimals))
        else:
            out.append(v)
    return out


def execute_stored_procedure(
    procedure_name: str,
    params: dict[str, Any],
    max_rows: int | None = None,
) -> SqlExecutionResult:
    """
    Execute a registered KPI stored procedure and return its result set.

    Unlike ``execute_sql_query``, this function bypasses the SQL guard because
    the procedure name comes from the trusted ``kpi_procedures`` registry, not
    from user-supplied text.  The parameter values are bound positionally via
    pyodbc to prevent injection.

    Parameters
    ----------
    procedure_name : str
        The DB object name, e.g. ``"GetABVai"``.
    params : dict[str, Any]
        Named parameters to pass (None values already stripped by the caller).
    max_rows : int | None
        Row cap; falls back to ``settings.max_result_rows``.
    """
    settings = get_settings()
    row_cap = max_rows or settings.max_result_rows

    # Build parameterised EXEC: EXEC kpi.GetABVai @date_preset=?, @brand=?, ...
    if params:
        param_placeholders = ", ".join(f"@{k}=?" for k in params)
        exec_sql = f"EXEC kpi.{procedure_name} {param_placeholders}"
        param_values = list(params.values())
    else:
        exec_sql = f"EXEC kpi.{procedure_name}"
        param_values = []

    started = perf_counter()
    try:
        with closing(get_connection()) as connection:
            try:
                connection.timeout = 30
            except Exception:
                pass
            cursor = connection.cursor()
            try:
                cursor.timeout = 30
            except Exception:
                pass

            if param_values:
                cursor.execute(exec_sql, param_values)
            else:
                cursor.execute(exec_sql)

            description = cursor.description or []
            columns = [col[0] for col in description]
            rows = cursor.fetchmany(row_cap)
    except Exception as exc:
        raise DatabaseExecutionError(str(exc)) from exc

    elapsed_ms = int((perf_counter() - started) * 1000)
    row_values = [_round_row(list(row)) for row in rows]

    return SqlExecutionResult(
        columns=columns,
        rows=row_values,
        row_count=len(row_values),
        elapsed_ms=elapsed_ms,
    )


def execute_sql_query(sql: str, max_rows: int | None = None) -> SqlExecutionResult:
    settings = get_settings()
    row_cap = max_rows or settings.max_result_rows

    safe_sql = ensure_safe_readonly_sql(sql)
    started = perf_counter()

    try:
        with closing(get_connection()) as connection:
            # Some pyodbc builds/drivers expose timeout only on connection,
            # not on cursor objects.
            try:
                connection.timeout = 30
            except Exception:
                pass

            cursor = connection.cursor()
            try:
                cursor.timeout = 30  # query execution timeout in seconds
            except Exception:
                pass
            cursor.execute(safe_sql)

            description = cursor.description or []
            columns = [column[0] for column in description]
            rows = cursor.fetchmany(row_cap)
    except Exception as exc:
        raise DatabaseExecutionError(str(exc)) from exc

    elapsed_ms = int((perf_counter() - started) * 1000)
    row_values = [_round_row(list(row)) for row in rows]

    return SqlExecutionResult(
        columns=columns,
        rows=row_values,
        row_count=len(row_values),
        elapsed_ms=elapsed_ms,
    )
