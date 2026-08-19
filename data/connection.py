"""SQL Server connection management.

Every connection is opened inside a context manager so that it is closed even
when a query raises. Write operations run inside an explicit transaction: a
dispense must either mark all of its units as dispensed or none of them.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import pyodbc

from config import get_config
from core.errors import DatabaseUnavailableError

logger = logging.getLogger(__name__)

#: Fail fast instead of leaving the operator staring at a frozen screen.
CONNECTION_TIMEOUT_SECONDS = 5
QUERY_TIMEOUT_SECONDS = 15

_UNAVAILABLE_MESSAGE = (
    "לא ניתן להתחבר לבסיס הנתונים. יש לוודא שהשירות SQL Server פועל ושההגדרות בקובץ .env נכונות."
)


def _open_connection(*, database: str | None = None, autocommit: bool) -> pyodbc.Connection:
    database_config = get_config().database
    try:
        connection = pyodbc.connect(
            database_config.connection_string(database),
            autocommit=autocommit,
            timeout=CONNECTION_TIMEOUT_SECONDS,
        )
    except pyodbc.Error as error:
        logger.error("Failed to connect to %s: %s", database_config.describe(), error)
        raise DatabaseUnavailableError(_UNAVAILABLE_MESSAGE) from error

    connection.timeout = QUERY_TIMEOUT_SECONDS
    return connection


@contextmanager
def transaction(database: str | None = None) -> Iterator[pyodbc.Connection]:
    """Run a unit of work atomically: commit on success, roll back on any error."""
    connection = _open_connection(database=database, autocommit=False)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


@contextmanager
def read_only_connection() -> Iterator[pyodbc.Connection]:
    """Open a connection for reporting queries that never modify data."""
    connection = _open_connection(autocommit=True)
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def server_connection() -> Iterator[pyodbc.Connection]:
    """Connect to "master", needed to create the application database itself.

    CREATE DATABASE cannot run inside a user transaction, hence autocommit.
    """
    connection = _open_connection(database="master", autocommit=True)
    try:
        yield connection
    finally:
        connection.close()


def wrap_driver_error(error: pyodbc.Error) -> DatabaseUnavailableError:
    """Convert a driver level failure into a message the operator can act on."""
    logger.error("Database operation failed: %s", error)
    return DatabaseUnavailableError(
        "הפעולה נכשלה בגלל שגיאה בבסיס הנתונים ולא בוצע שינוי. נא לנסות שוב."
    )
