"""Seed synthetic initial data for local development (NO real PII)."""

import asyncio

from sqlalchemy import select

from app.core.security import Role, hash_password
from app.db.models import Membership, Organization, User, Workspace
from app.db.session import AsyncSessionLocal


async def seed_data() -> None:
    """Idempotently create default organization, workspace, and synthetic demo users."""
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

        await db.commit()


if __name__ == "__main__":
    asyncio.run(seed_data())
    print("Seeded synthetic QualiText demo data.")
