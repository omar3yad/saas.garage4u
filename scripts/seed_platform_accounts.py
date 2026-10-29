#!/usr/bin/env python3
"""
Seed Platform Initial Accounts (SUPER_ADMIN and Babelooq OWNER)
Reference: docs/specs/phase-5-saas/spec.md (Section 17)
Task: 2.7 in docs/specs/phase-5-saas/tasks.md

Generates initial root accounts:
1. Platform Super Admin (superadmin): Platform-wide management, must_change_password=True.
2. Babelooq Owner (owner_babelooq): Fleet owner for owner_id=1, must_change_password=True.
3. Records PLATFORM_INIT event in audit_logs with system actor.
"""
import asyncio
import json
import secrets
import string
import sys
import os

# Add application root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bcrypt
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import text
from config import get_settings


def generate_secure_password(length: int = 16) -> str:
    """Generate a high-entropy random password with mixed character sets."""
    alphabet = string.ascii_letters + string.digits + "!@#$%&*-_"
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in pwd)
                and any(c.isupper() for c in pwd)
                and any(c.isdigit() for c in pwd)
                and any(c in "!@#$%&*-_" for c in pwd)):
            return pwd


def hash_password(plain: str) -> str:
    """Hash password with bcrypt (12 rounds)."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


async def main():
    settings = get_settings()
    db_url = settings.MIGRATION_DATABASE_URL or settings.DATABASE_URL

    print("=" * 65)
    print("PGMS SaaS — Platform Initial Accounts Provisioning")
    print("=" * 65)

    engine = create_async_engine(db_url)
    async with AsyncSession(engine) as session:
        # 1. Super Admin Account
        superadmin_pass = generate_secure_password(18)
        superadmin_hash = hash_password(superadmin_pass)

        # Check if superadmin already exists
        res = await session.execute(
            text("SELECT id FROM users WHERE username = 'superadmin';")
        )
        existing_superadmin = res.scalar()

        if not existing_superadmin:
            await session.execute(
                text("""
                    INSERT INTO users (
                        full_name, username, hashed_password, role,
                        gate_number, is_active, must_change_password,
                        owner_id, garage_id
                    ) VALUES (
                        'مدير المنصة الرئيسي', 'superadmin', :pwd_hash, 'SUPER_ADMIN',
                        NULL, TRUE, TRUE,
                        NULL, NULL
                    );
                """),
                {"pwd_hash": superadmin_hash}
            )
            created_superadmin = True
        else:
            created_superadmin = False

        # 2. Babelooq Owner Account
        owner_pass = generate_secure_password(18)
        owner_hash = hash_password(owner_pass)

        res = await session.execute(
            text("SELECT id FROM users WHERE username = 'owner_babelooq';")
        )
        existing_owner = res.scalar()

        if not existing_owner:
            await session.execute(
                text("""
                    INSERT INTO users (
                        full_name, username, hashed_password, role,
                        gate_number, is_active, must_change_password,
                        owner_id, garage_id
                    ) VALUES (
                        'مالك شركة باب اللوق', 'owner_babelooq', :pwd_hash, 'OWNER',
                        NULL, TRUE, TRUE,
                        1, NULL
                    );
                """),
                {"pwd_hash": owner_hash}
            )
            created_owner = True
        else:
            created_owner = False

        # 3. Audit Log Event (PLATFORM_INIT)
        payload = json.dumps({
            "event": "PLATFORM_INIT",
            "superadmin_created": created_superadmin,
            "owner_created": created_owner,
            "owner_id": 1,
            "must_change_password": True
        })

        await session.execute(
            text("""
                INSERT INTO audit_logs (
                    action, entity_type, entity_id, actor_type, actor_id,
                    owner_id, garage_id, payload_after
                ) VALUES (
                    'PLATFORM_INIT', 'platform', 1, 'system', NULL,
                    NULL, NULL, :payload
                );
            """),
            {"payload": payload}
        )

        await session.commit()

    await engine.dispose()

    # Print Credentials to Terminal (One-Time Output)
    print("\n[+] Super Admin Account Provisioned:")
    print("    Username: superadmin")
    if created_superadmin:
        print(f"    Temporary Password: {superadmin_pass}")
        print("    Status: Created (must_change_password = TRUE)")
    else:
        print("    Status: Already exists (password unchanged)")

    print("\n[+] Babelooq Owner Account Provisioned:")
    print("    Username: owner_babelooq")
    if created_owner:
        print(f"    Temporary Password: {owner_pass}")
        print("    Status: Created (must_change_password = TRUE, owner_id = 1)")
    else:
        print("    Status: Already exists (password unchanged)")

    print("\n[+] Audit Log: PLATFORM_INIT event recorded successfully.")
    print("=" * 65)
    print("NOTE: Store these credentials securely. Temporary passwords")
    print("must be changed upon first login via /auth/change-password.")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(main())
