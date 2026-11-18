"""Module A6: Data source connector abstractions (S3/MinIO, Database, SharePoint)."""

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field


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
        return config.uri.startswith("s3://")

    def fetch_bytes(self, config: ConnectorConfig) -> bytes:
        from app.storage.s3 import get_storage_backend

        if not self.validate_config(config):
            raise ValueError("URI S3 harus diawali dengan s3://")
        key = config.uri.removeprefix("s3://").split("/", 1)[-1]
        return get_storage_backend().get_bytes(key)


class DatabaseConnector:
    """Connector placeholder for read-only SQL extraction (configured per deployment)."""

    connector_type = "database"

    def validate_config(self, config: ConnectorConfig) -> bool:
        return config.uri.startswith(("postgresql://", "sqlite://"))

    def fetch_bytes(self, config: ConnectorConfig) -> bytes:
        raise NotImplementedError(
            "Database connector memerlukan driver & query terotorisasi di lingkungan target."
        )


class SharePointConnector:
    """Connector placeholder for Microsoft SharePoint document libraries."""

    connector_type = "sharepoint"

    def validate_config(self, config: ConnectorConfig) -> bool:
        return config.uri.startswith("https://")

    def fetch_bytes(self, config: ConnectorConfig) -> bytes:
        raise NotImplementedError(
            "SharePoint connector memerlukan OAuth client credentials dari admin."
        )


CONNECTOR_REGISTRY: dict[str, DataSourceConnector] = {
    "s3": S3Connector(),
    "database": DatabaseConnector(),
    "sharepoint": SharePointConnector(),
}
