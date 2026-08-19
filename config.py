"""Application configuration, loaded exclusively from environment variables.

No credential is ever hard-coded here: the database password (when SQL Server
authentication is used) and the session secret key are read from the process
environment, optionally populated from a local ".env" file that is git-ignored.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
LOG_DIRECTORY = PROJECT_ROOT / "logs"

load_dotenv(PROJECT_ROOT / ".env")


def _environment_value(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _environment_flag(name: str, default: bool = False) -> bool:
    raw = _environment_value(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _environment_port(name: str, default: int) -> int:
    raw = _environment_value(name)
    if not raw:
        return default
    try:
        port = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be a whole number, got {raw!r}") from error
    if not 1 <= port <= 65535:
        raise ValueError(f"{name} must be between 1 and 65535, got {port}")
    return port


@dataclass(frozen=True)
class DatabaseConfig:
    """Everything needed to build an ODBC connection string for SQL Server."""

    server: str
    database: str
    driver: str
    username: str
    password: str
    encrypt: str
    trust_server_certificate: str

    @property
    def uses_windows_authentication(self) -> bool:
        return not self.username

    def connection_string(self, database: str | None = None) -> str:
        """Build a connection string, optionally overriding the target database.

        The override is used to reach the "master" database while creating the
        application database for the first time.
        """
        parts = [
            f"DRIVER={{{self.driver}}}",
            f"SERVER={self.server}",
            f"DATABASE={database or self.database}",
            f"Encrypt={self.encrypt}",
            f"TrustServerCertificate={self.trust_server_certificate}",
        ]
        if self.uses_windows_authentication:
            parts.append("Trusted_Connection=yes")
        else:
            parts.append(f"UID={self.username}")
            parts.append(f"PWD={self.password}")
        return ";".join(parts)

    def describe(self) -> str:
        """Human readable summary that never exposes the password."""
        authentication = "Windows" if self.uses_windows_authentication else f"SQL login ({self.username})"
        return f"{self.server} / {self.database} [{authentication}]"


@dataclass(frozen=True)
class WebConfig:
    secret_key: str
    host: str
    port: int
    debug: bool
    secret_key_was_generated: bool


@dataclass(frozen=True)
class AppConfig:
    database: DatabaseConfig
    web: WebConfig
    log_file: Path


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Return the immutable application configuration (read once per process)."""
    database = DatabaseConfig(
        server=_environment_value("BECS_DB_SERVER", r"localhost\SQLEXPRESS"),
        database=_environment_value("BECS_DB_NAME", "BloodBank"),
        driver=_environment_value("BECS_DB_DRIVER", "ODBC Driver 18 for SQL Server"),
        username=_environment_value("BECS_DB_USER"),
        password=os.environ.get("BECS_DB_PASSWORD", ""),
        encrypt=_environment_value("BECS_DB_ENCRYPT", "no"),
        trust_server_certificate=_environment_value("BECS_DB_TRUST_CERTIFICATE", "yes"),
    )

    configured_secret = _environment_value("BECS_SECRET_KEY")
    web = WebConfig(
        secret_key=configured_secret or secrets.token_hex(32),
        host=_environment_value("BECS_HOST", "127.0.0.1"),
        port=_environment_port("BECS_PORT", 5000),
        debug=_environment_flag("BECS_DEBUG", False),
        secret_key_was_generated=not configured_secret,
    )

    LOG_DIRECTORY.mkdir(exist_ok=True)
    return AppConfig(database=database, web=web, log_file=LOG_DIRECTORY / "becs.log")
