"""Object storage abstraction supporting S3/MinIO and local filesystem fast-dev fallback."""

import shutil
from pathlib import Path
from typing import BinaryIO, Protocol

import boto3
from botocore.client import BaseClient

from app.core.config import get_settings


class StorageBackend(Protocol):
    """Minimal object storage interface for dataset Parquet files, exports, and reports."""

    def put_bytes(self, key: str, data: bytes) -> str:
        """Persist bytes at `key` and return the storage URI."""
        ...

    def put_file(self, key: str, fileobj: BinaryIO) -> str:
        """Stream bytes from `fileobj` to `key` and return the storage URI."""
        ...

    def get_bytes(self, key: str) -> bytes:
        """Read bytes stored at `key`."""
        ...

    def exists(self, key: str) -> bool:
        """Return True if `key` exists."""
        ...

    def delete_key(self, key: str) -> None:
        """Delete object at `key` if present."""
        ...


class LocalStorageBackend:
    """Filesystem storage backend for fast local development without MinIO."""

    def __init__(self, root_dir: str) -> None:
        self.root = Path(root_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve_safe(self, key: str) -> Path:
        clean_key = key.lstrip("/")
        target = (self.root / clean_key).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("Invalid storage key path traversal attempt.")
        return target

    def put_bytes(self, key: str, data: bytes) -> str:
        target = self._resolve_safe(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return f"local://{key.lstrip('/')}"

    def put_file(self, key: str, fileobj: BinaryIO) -> str:
        target = self._resolve_safe(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as out:
            shutil.copyfileobj(fileobj, out)
        return f"local://{key.lstrip('/')}"

    def get_bytes(self, key: str) -> bytes:
        target = self._resolve_safe(key)
        return target.read_bytes()

    def exists(self, key: str) -> bool:
        return self._resolve_safe(key).exists()

    def delete_key(self, key: str) -> None:
        self._resolve_safe(key).unlink(missing_ok=True)


class S3StorageBackend:
    """S3 / MinIO object storage backend for Docker and production deployments."""

    def __init__(
        self,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket_name: str,
        region: str,
    ) -> None:
        self.bucket = bucket_name
        self.client: BaseClient = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

    def put_bytes(self, key: str, data: bytes) -> str:
        clean_key = key.lstrip("/")
        self.client.put_object(Bucket=self.bucket, Key=clean_key, Body=data)
        return f"s3://{self.bucket}/{clean_key}"

    def put_file(self, key: str, fileobj: BinaryIO) -> str:
        clean_key = key.lstrip("/")
        self.client.upload_fileobj(fileobj, self.bucket, clean_key)
        return f"s3://{self.bucket}/{clean_key}"

    def get_bytes(self, key: str) -> bytes:
        clean_key = key.lstrip("/")
        resp = self.client.get_object(Bucket=self.bucket, Key=clean_key)
        body: bytes = resp["Body"].read()
        return body

    def exists(self, key: str) -> bool:
        clean_key = key.lstrip("/")
        try:
            self.client.head_object(Bucket=self.bucket, Key=clean_key)
            return True
        except Exception:
            return False

    def delete_key(self, key: str) -> None:
        clean_key = key.lstrip("/")
        self.client.delete_object(Bucket=self.bucket, Key=clean_key)


def get_storage_backend() -> StorageBackend:
    """Return configured storage backend (`local` for fast-dev or `s3` for MinIO/S3)."""
    settings = get_settings()
    if settings.storage_backend == "s3":
        return S3StorageBackend(
            endpoint_url=settings.s3_endpoint_url,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            bucket_name=settings.s3_bucket_name,
            region=settings.s3_region,
        )
    return LocalStorageBackend(settings.local_storage_path)
