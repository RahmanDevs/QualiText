"""Benchmark / evaluation set management endpoints for contamination detection (Module C5)."""

import json
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_workspace_permission
from app.core.errors import AppError
from app.core.security import Permission
from app.db.models import BenchmarkSet, User
from app.db.session import get_db
from app.services.audit_service import extract_client_ip, record_audit_log
from app.storage.s3 import get_storage_backend

router = APIRouter(tags=["benchmarks"])
MAX_BENCHMARK_UPLOAD_BYTES = 20 * 1024 * 1024


class BenchmarkSetCreateRequest(BaseModel):
    """Create a benchmark reference set from an explicit prompt list."""

    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    prompts: list[str] = Field(min_length=1)


class BenchmarkSetSummary(BaseModel):
    """Summary metadata for a benchmark reference set."""

    id: str
    workspace_id: str
    name: str
    description: str
    entry_count: int
    is_active: bool
    created_at: datetime


def _to_summary(item: BenchmarkSet) -> BenchmarkSetSummary:
    return BenchmarkSetSummary(
        id=item.id,
        workspace_id=item.workspace_id,
        name=item.name,
        description=item.description,
        entry_count=item.entry_count,
        is_active=item.is_active,
        created_at=item.created_at,
    )


def _normalize_prompts(raw_prompts: list[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in raw_prompts:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            cleaned.append(text)
    return cleaned


def _extract_prompts_from_bytes(raw_bytes: bytes, filename: str) -> list[str]:
    text = raw_bytes.decode("utf-8", errors="replace")
    lower_name = filename.lower()
    extracted: list[str] = []
    if lower_name.endswith(".jsonl"):
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                extracted.append(stripped)
                continue
            if isinstance(obj, dict):
                for key in ("prompt", "question", "instruction", "input", "text"):
                    val = obj.get(key)
                    if isinstance(val, str) and val.strip():
                        extracted.append(val.strip())
                        break
            elif isinstance(obj, str) and obj.strip():
                extracted.append(obj.strip())
    else:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                extracted.append(stripped)
    return _normalize_prompts(extracted)


@router.get("/workspaces/{workspace_id}/benchmarks", response_model=list[BenchmarkSetSummary])
async def list_benchmark_sets(
    workspace_id: str,
    _user: User = Depends(require_workspace_permission(Permission.DATASET_READ)),
    db: AsyncSession = Depends(get_db),
) -> list[BenchmarkSetSummary]:
    """List active and archived benchmark sets in a workspace."""
    items = (
        (
            await db.execute(
                select(BenchmarkSet)
                .where(BenchmarkSet.workspace_id == workspace_id)
                .order_by(BenchmarkSet.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_to_summary(item) for item in items]


@router.post(
    "/workspaces/{workspace_id}/benchmarks",
    response_model=BenchmarkSetSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_benchmark_set(
    workspace_id: str,
    payload: BenchmarkSetCreateRequest,
    request: Request,
    user: User = Depends(require_workspace_permission(Permission.POLICY_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> BenchmarkSetSummary:
    """Register an evaluation benchmark set from a JSON list of prompts."""
    prompts = _normalize_prompts(payload.prompts)
    if not prompts:
        raise AppError(
            code="EMPTY_BENCHMARK",
            message="Set benchmark minimal memiliki satu teks prompt non-kosong.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    item = BenchmarkSet(
        workspace_id=workspace_id,
        name=payload.name.strip(),
        description=payload.description.strip(),
        entry_count=len(prompts),
        prompts_json=prompts,
        storage_uri="",
        is_active=True,
        created_by=user.id,
    )
    db.add(item)
    await db.flush()
    await record_audit_log(
        db,
        action="benchmark.create",
        target_type="benchmark_set",
        target_id=item.id,
        user_id=user.id,
        workspace_id=workspace_id,
        ip_address=extract_client_ip(request),
        metadata={"name": item.name, "entry_count": item.entry_count},
    )
    await db.commit()
    await db.refresh(item)
    return _to_summary(item)


@router.post(
    "/workspaces/{workspace_id}/benchmarks/upload",
    response_model=BenchmarkSetSummary,
    status_code=status.HTTP_201_CREATED,
)
async def upload_benchmark_set(
    workspace_id: str,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    file: UploadFile = File(...),
    user: User = Depends(require_workspace_permission(Permission.POLICY_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> BenchmarkSetSummary:
    """Upload a JSONL or plain-text evaluation benchmark file for contamination scanning."""
    raw_bytes = await file.read(MAX_BENCHMARK_UPLOAD_BYTES + 1)
    if len(raw_bytes) > MAX_BENCHMARK_UPLOAD_BYTES:
        raise AppError(
            code="BENCHMARK_TOO_LARGE",
            message="Ukuran berkas benchmark maksimal 20 MB.",
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        )

    prompts = _extract_prompts_from_bytes(raw_bytes, file.filename or "benchmark.txt")
    if not prompts:
        raise AppError(
            code="EMPTY_BENCHMARK",
            message="Tidak ada prompt benchmark yang dapat diekstrak dari berkas.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    bench_id = str(uuid.uuid4())
    storage_key = f"benchmarks/{workspace_id}/{bench_id}/source.txt"
    storage_uri = get_storage_backend().put_bytes(storage_key, raw_bytes)

    item = BenchmarkSet(
        id=bench_id,
        workspace_id=workspace_id,
        name=name.strip() or "Benchmark",
        description=description.strip(),
        entry_count=len(prompts),
        prompts_json=prompts,
        storage_uri=storage_uri,
        is_active=True,
        created_by=user.id,
    )
    db.add(item)
    await db.flush()
    await record_audit_log(
        db,
        action="benchmark.upload",
        target_type="benchmark_set",
        target_id=item.id,
        user_id=user.id,
        workspace_id=workspace_id,
        ip_address=extract_client_ip(request),
        metadata={"name": item.name, "entry_count": item.entry_count},
    )
    await db.commit()
    await db.refresh(item)
    return _to_summary(item)


@router.delete(
    "/workspaces/{workspace_id}/benchmarks/{benchmark_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_benchmark_set(
    workspace_id: str,
    benchmark_id: str,
    request: Request,
    user: User = Depends(require_workspace_permission(Permission.POLICY_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Delete a benchmark reference set and its stored raw artifact."""
    item = (
        await db.execute(
            select(BenchmarkSet).where(
                BenchmarkSet.id == benchmark_id,
                BenchmarkSet.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise AppError(
            code="BENCHMARK_NOT_FOUND",
            message="Set benchmark tidak ditemukan.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    storage_key = f"benchmarks/{workspace_id}/{item.id}/source.txt"
    if item.storage_uri:
        storage = get_storage_backend()
        if storage.exists(storage_key):
            storage.delete_key(storage_key)

    await record_audit_log(
        db,
        action="benchmark.delete",
        target_type="benchmark_set",
        target_id=item.id,
        user_id=user.id,
        workspace_id=workspace_id,
        ip_address=extract_client_ip(request),
        metadata={"name": item.name},
    )
    await db.delete(item)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
