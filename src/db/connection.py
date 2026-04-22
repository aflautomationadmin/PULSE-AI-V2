from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

from src.config import get_settings

# Hard Python-level cap — the ODBC driver timeout is not always respected.
_CONNECT_TIMEOUT_SECONDS = 15


def get_connection():
    try:
        import pyodbc  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError("pyodbc is required. Install dependencies first.") from exc

    settings = get_settings()

    conn_str = (
        f"DRIVER={settings.db_driver};"
        f"SERVER={settings.db_server};"
        f"DATABASE={settings.db_database};"
        f"UID={settings.db_username};"
        f"PWD={settings.db_password};"
        "Authentication=ActiveDirectoryPassword;"
        "TrustServerCertificate=yes;"
        f"Connection Timeout={_CONNECT_TIMEOUT_SECONDS};"
        f"LoginTimeout={_CONNECT_TIMEOUT_SECONDS};"
    )

    # Run connect() in a thread so we can enforce a hard Python-level timeout
    # independent of what the ODBC driver decides to do.
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(pyodbc.connect, conn_str)
        try:
            return future.result(timeout=_CONNECT_TIMEOUT_SECONDS + 2)
        except FuturesTimeoutError:
            raise RuntimeError(
                f"Database connection timed out after {_CONNECT_TIMEOUT_SECONDS}s. "
                "Check DB_SERVER, credentials, and network access in your .env file."
            )
