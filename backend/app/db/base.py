"""SQLAlchemy declarative base and shared column/mixin definitions."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    """Return timezone-aware UTC timestamp."""
    return datetime.now(UTC)


def new_uuid_str() -> str:
    """Generate a string UUID4 compatible with both PostgreSQL and SQLite."""
    return str(uuid4())


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""


class TimestampMixin:
    """Standard created_at / updated_at columns."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class UUIDPrimaryKeyMixin:
    """String-backed UUID primary key for cross-database fast-dev compatibility."""

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid_str)
