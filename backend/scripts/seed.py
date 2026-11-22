"""Seed synthetic initial data for local development (NO real PII)."""

import asyncio
import json

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.security import Role, hash_password
from app.db.models import BenchmarkSet, Dataset, Membership, Organization, User, Workspace
from app.db.session import AsyncSessionLocal
from app.services import dataset_service


async def seed_data() -> None:
    """Idempotently create default organization, workspace, and 5 synthetic RBAC demo users."""
    settings = get_settings()
    configure_logging(log_level=settings.log_level, log_format=settings.log_format)
    logger = get_logger(__name__)

    async with AsyncSessionLocal() as db:
        org = (
            await db.execute(select(Organization).where(Organization.slug == "qualitext-demo"))
        ).scalar_one_or_none()
        if org is None:
            org = Organization(name="QualiText Demo Corp", slug="qualitext-demo", settings={})
            db.add(org)
            await db.flush()

        ws = (
            await db.execute(
                select(Workspace).where(
                    Workspace.organization_id == org.id,
                    Workspace.slug == "tim-nlp-inti",
                )
            )
        ).scalar_one_or_none()
        if ws is None:
            ws = Workspace(
                organization_id=org.id,
                name="Tim NLP Inti",
                slug="tim-nlp-inti",
                settings={"default_locale": "id"},
            )
            db.add(ws)
            await db.flush()

        demo_users = [
            ("admin@qualitext.id", "Admin QualiText", "Admin123!", Role.ADMIN, True),
            (
                "dina@qualitext.id",
                "Dina (Data Quality Analyst)",
                "Analyst123!",
                Role.ANALYST,
                False,
            ),
            ("raka@qualitext.id", "Raka (Data Steward)", "Curator123!", Role.CURATOR, False),
            (
                "sari@qualitext.id",
                "Sari (Compliance Lead)",
                "Governance123!",
                Role.GOVERNANCE,
                False,
            ),
            (
                "viewer@qualitext.id",
                "Budi (Audit Viewer)",
                "Viewer123!",
                Role.VIEWER,
                False,
            ),
        ]

        for email, full_name, password, role, is_superadmin in demo_users:
            user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
            if user is None:
                user = User(
                    email=email,
                    full_name=full_name,
                    password_hash=hash_password(password),
                    is_active=True,
                    is_superadmin=is_superadmin,
                )
                db.add(user)
                await db.flush()

            membership = (
                await db.execute(
                    select(Membership).where(
                        Membership.user_id == user.id,
                        Membership.workspace_id == ws.id,
                    )
                )
            ).scalar_one_or_none()
            if membership is None:
                db.add(Membership(user_id=user.id, workspace_id=ws.id, role=role.value))

        admin_user = (
            await db.execute(select(User).where(User.email == "admin@qualitext.id"))
        ).scalar_one()

        existing_ds = (
            (await db.execute(select(Dataset).where(Dataset.workspace_id == ws.id)))
            .scalars()
            .first()
        )
        if existing_ds is None:
            demo_ds = await dataset_service.create_dataset(
                db,
                workspace_id=ws.id,
                name="Korpus Instruksi Bahasa Indonesia (Demo)",
                description="Dataset SFT sintetis aman tanpa PII untuk demonstrasi pipeline.",
                dataset_type="sft",
                retention_days=90,
                user_id=admin_user.id,
            )
            sample_rows = [
                {
                    "prompt": "Jelaskan perbedaan supervised fine-tuning dan DPO secara ringkas.",
                    "response": (
                        "Supervised fine-tuning melatih model pada pasangan contoh terkurasi, "
                        "sedangkan DPO mengoptimalkan preferensi antar respons."
                    ),
                },
                {
                    "prompt": "Berikan 2 tips menjaga kebersihan korpus RAG berbahasa Indonesia.",
                    "response": (
                        "Pastikan potongan dokumen memiliki batas kalimat utuh dan bebas dari "
                        "duplikat maupun fragmen HTML."
                    ),
                },
                {
                    "prompt": "Mengapa deduplikasi semantik penting sebelum pelatihan LLM?",
                    "response": (
                        "Deduplikasi mencegah model menghafal teks berulang dan meningkatkan "
                        "generalisasi evaluasi."
                    ),
                },
            ]
            raw_jsonl = "\n".join(json.dumps(r, ensure_ascii=False) for r in sample_rows) + "\n"
            await dataset_service.direct_upload_version(
                db,
                dataset=demo_ds,
                source_format="jsonl",
                raw_bytes=raw_jsonl.encode("utf-8"),
                user_id=admin_user.id,
            )

        existing_bench = (
            (await db.execute(select(BenchmarkSet).where(BenchmarkSet.workspace_id == ws.id)))
            .scalars()
            .first()
        )
        if existing_bench is None:
            db.add(
                BenchmarkSet(
                    workspace_id=ws.id,
                    name="Evaluasi Dasar NLP Indonesia (Demo)",
                    description="Referensi sintetis untuk deteksi kontaminasi benchmark.",
                    entry_count=2,
                    prompts_json=[
                        "Sebutkan ibu kota resmi Republik Indonesia dan pulau lokasinya.",
                        "Jelaskan prinsip minimasi data pada perlindungan data pribadi.",
                    ],
                    storage_uri="",
                    is_active=True,
                    created_by=admin_user.id,
                )
            )

        await db.commit()
        logger.info("seeded_demo_data", workspace_slug=ws.slug, user_count=len(demo_users))


if __name__ == "__main__":
    asyncio.run(seed_data())
