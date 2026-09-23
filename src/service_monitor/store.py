"""Optional MySQL metadata store.

The original monitors kept three tables in MySQL:

* ``*_environment``   - ZooKeeper address/root per test environment;
* ``*_interface``     - interface -> application/host/port inventory;
* ``*_application``   - owning team contact (name/phone/email) per application.

Owner lookup and inventory persistence were hard-wired to those tables with
string-interpolated SQL. This module keeps the same capability but:

* is fully optional (``pymysql`` is imported lazily);
* uses parameterized queries everywhere (no SQL injection);
* reads connection settings from environment variables only.

See ``sql/ddl.sql`` for a sanitized table definition you can adapt.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from .models import Owner

logger = logging.getLogger(__name__)


class StoreError(Exception):
    """Raised when the metadata store cannot be used."""


@dataclass
class StoreConfig:
    """Connection settings, normally built from environment variables."""

    host: str
    port: int = 3306
    user: str = ""
    password: str = ""
    database: str = ""
    charset: str = "utf8mb4"
    connect_timeout: int = 5

    @classmethod
    def from_env(cls, prefix: str = "MYSQL_") -> "StoreConfig":
        host = os.environ.get(f"{prefix}HOST", "").strip()
        if not host:
            raise StoreError(f"{prefix}HOST is not set; metadata store disabled")
        return cls(
            host=host,
            port=int(os.environ.get(f"{prefix}PORT", "3306") or 3306),
            user=os.environ.get(f"{prefix}USER", "").strip(),
            password=os.environ.get(f"{prefix}PASSWORD", ""),
            database=os.environ.get(f"{prefix}DATABASE", "").strip(),
        )


class MetadataStore:
    """Thin wrapper over the three monitor tables.

    ``connection_factory`` allows tests to inject a fake DB-API connection.
    """

    def __init__(self, config: StoreConfig, connection_factory: Any = None) -> None:
        self.config = config
        self._factory = connection_factory or self._default_factory

    # ------------------------------------------------------------------
    def _default_factory(self) -> Any:
        try:
            import pymysql
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise StoreError(
                "pymysql is required for the metadata store: pip install PyMySQL"
            ) from exc
        return pymysql.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
            database=self.config.database,
            charset=self.config.charset,
            connect_timeout=self.config.connect_timeout,
            autocommit=True,
        )

    def _connect(self) -> Any:
        try:
            return self._factory()
        except StoreError:
            raise
        except Exception as exc:
            raise StoreError(f"cannot connect to metadata store: {exc}") from exc

    # ------------------------------------------------------------------
    # owner lookup (mirrors the original getalarmInfo join)
    # ------------------------------------------------------------------
    def owner_for_interface(self, interface: str) -> Optional[Owner]:
        """Join interface -> application -> contact; returns None when unknown."""
        sql = (
            "SELECT a.application_desc, a.user_name, a.user_phone, a.user_mail, a.send_flag "
            "FROM monitor_application a "
            "JOIN monitor_interface b ON a.application = b.application "
            "WHERE b.interface = %s LIMIT 1"
        )
        row = self._query_one(sql, (interface,))
        if not row:
            return None
        desc, name, phone, mail, send_flag = row
        if send_flag is not None and int(send_flag) == 0:
            return None  # explicitly muted in the source table
        return Owner(phone=(phone or None), name=(name or desc or None), email=(mail or None))

    # ------------------------------------------------------------------
    # inventory upsert (mirrors the original insertPrivideInfoIntoDb)
    # ------------------------------------------------------------------
    def upsert_inventory(self, rows: Sequence[Dict[str, Any]]) -> int:
        """Insert or refresh interface/application rows; returns rows touched."""
        touched = 0
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                for row in rows:
                    interface = str(row.get("interface") or "").strip()
                    if not interface:
                        continue
                    application = str(row.get("application") or "").strip()
                    host = str(row.get("host") or row.get("hostname") or "").strip()
                    port = int(row.get("port") or 0)
                    touched += self._upsert_interface(cursor, interface, application, host, port)
                    if application:
                        self._ensure_application(cursor, application)
        finally:
            _close(conn)
        return touched

    @staticmethod
    def _upsert_interface(cursor: Any, interface: str, application: str, host: str, port: int) -> int:
        cursor.execute(
            "SELECT COUNT(1) FROM monitor_interface WHERE interface = %s", (interface,)
        )
        exists = (cursor.fetchone() or [0])[0]
        if exists:
            cursor.execute(
                "UPDATE monitor_interface SET application = %s, ips = %s, port = %s "
                "WHERE interface = %s",
                (application, host, port, interface),
            )
        else:
            cursor.execute(
                "INSERT INTO monitor_interface (interface, application, ips, port) "
                "VALUES (%s, %s, %s, %s)",
                (interface, application, host, port),
            )
        return 1

    @staticmethod
    def _ensure_application(cursor: Any, application: str) -> None:
        cursor.execute(
            "INSERT IGNORE INTO monitor_application (application) VALUES (%s)",
            (application,),
        )

    # ------------------------------------------------------------------
    def _query_one(self, sql: str, params: tuple) -> Optional[tuple]:
        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchone()
        finally:
            _close(conn)


def _close(conn: Any) -> None:
    try:
        conn.close()
    except Exception:  # pragma: no cover - defensive
        pass


__all__ = ["MetadataStore", "StoreConfig", "StoreError"]
