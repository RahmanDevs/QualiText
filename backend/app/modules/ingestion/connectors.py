"""Module A6: Data source connector abstractions (S3/MinIO, Database, SharePoint)."""

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.services.webhook_service import is_safe_webhook_url

_SQL_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FORBIDDEN_SQL_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|DETACH|PRAGMA|REPLACE|VACUUM)\b",
    re.IGNORECASE,
)
_CRED_REF_RE = re.compile(r"^[A-Z0-9_]{1,64}$")
MAX_CONNECTOR_BYTES = 200 * 1024 * 1024


class ConnectorConfig(BaseModel):
    """Validated configuration for external/internal data source connectors."""

    connector_type: Literal["s3", "database", "sharepoint"]
    uri: str = Field(min_length=1)
    credentials_ref: str = ""
    options: dict[str, Any] = Field(default_factory=dict)


class DataSourceConnector(Protocol):
    """Interface for pulling raw dataset bytes from an external/internal source."""

    connector_type: str

    def validate_config(self, config: ConnectorConfig) -> bool:
        """Validate connector configuration without leaking credentials."""
        ...

    def fetch_bytes(self, config: ConnectorConfig) -> bytes:
        """Fetch raw dataset bytes from the configured source."""
        ...


class S3Connector:
    """Connector for S3/MinIO object storage URIs (`s3://bucket/key`)."""

    connector_type = "s3"

    def validate_config(self, config: ConnectorConfig) -> bool:
        if not config.uri.startswith("s3://"):
            return False
        rest = config.uri.removeprefix("s3://")
        parts = rest.split("/", 1)
        if len(parts) < 2 or not parts[0] or not parts[1]:
            return False
        return ".." not in Path(parts[1]).parts

    def fetch_bytes(self, config: ConnectorConfig) -> bytes:
        from app.storage.s3 import get_storage_backend

        if not self.validate_config(config):
            raise ValueError("URI S3 harus berbentuk s3://bucket/key tanpa path traversal")
        key = config.uri.removeprefix("s3://").split("/", 1)[-1]
        data = get_storage_backend().get_bytes(key)
        if len(data) > MAX_CONNECTOR_BYTES:
            raise ValueError("Ukuran objek melebihi batas maksimal 200 MB")
        return data


def _build_readonly_sql(config: ConnectorConfig) -> tuple[str, int]:
    """Validate read-only SQL table or SELECT statement and return (query, limit)."""
    limit_raw = config.options.get("limit", 10_000)
    try:
        limit = max(1, min(100_000, int(limit_raw)))
    except (TypeError, ValueError) as exc:
        raise ValueError("Parameter limit konektor database harus berupa bilangan bulat") from exc

    table = config.options.get("table")
    query = config.options.get("query")
    if isinstance(table, str) and table.strip():
        clean_table = table.strip()
        if not _SQL_IDENT_RE.fullmatch(clean_table):
            raise ValueError("Nama tabel database tidak valid")
        return f'SELECT * FROM "{clean_table}" LIMIT ?', limit

    if isinstance(query, str) and query.strip():
        clean_query = query.strip().rstrip(";").strip()
        if ";" in clean_query:
            raise ValueError("Multi-statement SQL tidak diizinkan")
        if not re.match(r"(?i)^SELECT\s+", clean_query):
            raise ValueError("Hanya query SELECT read-only yang diizinkan")
        if _FORBIDDEN_SQL_RE.search(clean_query):
            raise ValueError("Kata kunci SQL mutasi data tidak diizinkan")
        return f"SELECT * FROM ({clean_query}) AS _qt_sub LIMIT ?", limit

    raise ValueError("Konektor database membutuhkan opsi 'table' atau 'query' SELECT")


class DatabaseConnector:
    """Read-only SQL connector (SQLite read-only URI or authorized SELECT query -> JSONL)."""

    connector_type = "database"

    def validate_config(self, config: ConnectorConfig) -> bool:
        if not config.uri.startswith(("postgresql://", "sqlite://", "sqlite+pysqlite://")):
            return False
        try:
            _build_readonly_sql(config)
        except ValueError:
            return False
        return True

    def fetch_bytes(self, config: ConnectorConfig) -> bytes:
        if not self.validate_config(config):
            raise ValueError("Konfigurasi konektor database tidak valid")
        sql_stmt, limit = _build_readonly_sql(config)

        if config.uri.startswith("postgresql://"):
            raise ValueError(
                "Driver sinkron PostgreSQL belum dikonfigurasi pada runtime offline ini; "
                "gunakan URI SQLite read-only atau unggahan berkas."
            )

        raw_path = config.uri.split(":///", 1)[1] if ":///" in config.uri else ""
        if not raw_path:
            raise ValueError("URI SQLite harus menyertakan path berkas (sqlite:///path/ke/db)")

        if raw_path == ":memory:":
            conn = sqlite3.connect(":memory:")
        else:
            db_path = Path(raw_path).resolve()
            app_db_url = get_settings().database_url
            if app_db_url.startswith("sqlite") and ":///" in app_db_url:
                app_raw_path = app_db_url.split(":///", 1)[1].split("?", 1)[0]
                if (
                    app_raw_path
                    and app_raw_path != ":memory:"
                    and db_path == Path(app_raw_path).resolve()
                ):
                    raise ValueError(
                        "Akses konektor ke basis data internal aplikasi tidak diizinkan"
                    )
            if not db_path.is_file():
                raise ValueError("Berkas SQLite target tidak ditemukan")
            conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)

        try:
            cursor = conn.execute(sql_stmt, (limit,))
            col_names = [desc[0] for desc in (cursor.description or [])]
            lines: list[str] = []
            for row in cursor:
                record = dict(zip(col_names, row, strict=True))
                lines.append(json.dumps(record, ensure_ascii=False, default=str))
        finally:
            conn.close()

        payload = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
        if len(payload) > MAX_CONNECTOR_BYTES:
            raise ValueError("Hasil ekstraksi database melebihi batas maksimal 200 MB")
        return payload


def _resolve_sharepoint_token(config: ConnectorConfig) -> str:
    """Resolve OAuth Bearer token from explicit options or whitelisted environment key."""
    opt_token = config.options.get("access_token")
    if isinstance(opt_token, str) and opt_token.strip():
        return opt_token.strip()
    if config.credentials_ref:
        ref = config.credentials_ref.strip().upper()
        if not ref.startswith("QUALITEXT_") or not _CRED_REF_RE.fullmatch(ref):
            raise ValueError(
                "credentials_ref harus diawali 'QUALITEXT_' dengan karakter alfanumerik"
            )
        env_token = os.environ.get(ref, "").strip()
        if env_token:
            return env_token
    raise ValueError("SharePoint connector memerlukan OAuth Bearer token yang valid")


class SharePointConnector:
    """HTTPS + OAuth Bearer connector for Microsoft SharePoint document libraries."""

    connector_type = "sharepoint"

    def validate_config(self, config: ConnectorConfig) -> bool:
        try:
            parsed = urlparse(config.uri.strip())
        except ValueError:
            return False
        if parsed.scheme != "https" or not parsed.hostname:
            return False
        allow_private = get_settings().webhook_allow_private_targets
        return is_safe_webhook_url(config.uri, allow_private=allow_private)

    def fetch_bytes(self, config: ConnectorConfig) -> bytes:
        if not self.validate_config(config):
            raise ValueError("URI SharePoint harus menggunakan HTTPS non-privat yang valid")
        token = _resolve_sharepoint_token(config)
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/octet-stream, application/json",
        }
        with httpx.Client(timeout=30.0, follow_redirects=False) as client:
            response = client.get(config.uri, headers=headers)
        if response.status_code >= 400:
            raise ValueError(
                f"Permintaan SharePoint gagal dengan status HTTP {response.status_code}"
            )
        data = response.content
        if len(data) > MAX_CONNECTOR_BYTES:
            raise ValueError("Berkas SharePoint melebihi batas maksimal 200 MB")
        return data


CONNECTOR_REGISTRY: dict[str, DataSourceConnector] = {
    "s3": S3Connector(),
    "database": DatabaseConnector(),
    "sharepoint": SharePointConnector(),
}
