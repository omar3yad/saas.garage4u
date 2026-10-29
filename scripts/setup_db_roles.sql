-- ==============================================================================
-- PGMS SaaS - Phase 5: Database Roles & Default Privileges Setup
-- Reference: docs/specs/phase-5-saas/spec.md (Section 4.1)
-- Tasks: 1.1 & 1.2 in docs/specs/phase-5-saas/tasks.md
-- ==============================================================================
-- 
-- ARCHITECTURAL DECISIONS & SECURITY BOUNDARIES:
-- ----------------------------------------------
-- 1. Role Segregation:
--    - pgms_migrator:
--        * Administrative migration and maintenance role (Alembic, seed, pg_dump).
--        * Attributes: LOGIN, CREATEDB, BYPASSRLS, NOSUPERUSER.
--        * BYPASSRLS is required so migrations and backfill scripts can inspect,
--          alter, and populate tables without tripping Row-Level Security policies.
--    - pgms_app:
--        * Runtime day-to-day role for FastAPI application connections (DATABASE_URL).
--        * Attributes: LOGIN, NOBYPASSRLS, NOSUPERUSER.
--        * Strictly subject to Row-Level Security (RLS) enforcement on all 11 operational tables.
--
-- 2. Privileges on RLS-Exempt Tables (Resolution B2):
--    - The 5 RLS-exempt tables are:
--        1. owners: Platform-wide entity managed by SUPER_ADMIN; scoped by owner_id.
--        2. garages: Metadata read before setting garage context; checks status & code.
--        3. users: Authentication lookup before garage resolution; scoped by garage_id in app logic.
--        4. garage_counters: Managed with atomic exclusive row locks inside service transactions.
--        5. edge_devices: Camera WebSocket/upload auth via token_hash before garage context.
--    - Decision: pgms_app is granted SELECT, INSERT, UPDATE across tables (including these 5)
--      because SUPER_ADMIN and OWNER personas need to register and update these records.
--    - Logical Isolation & Anti-Tampering:
--      Cross-tenant tampering prevention on these 5 tables is strictly enforced at the
--      APPLICATION LAYER via Scoped Repositories and validation dependencies.
--    - Hardened Deletion Constraints:
--      REVOKE DELETE, TRUNCATE on owners, garages, users, and REVOKE UPDATE, DELETE, TRUNCATE
--      on audit_logs will be executed post-migration in scripts/grant_app_privileges.sql
--      once those tables exist physically.
--
-- 3. Default Privileges Strategy (Resolution B1):
--    - Future tables and sequences created by pgms_migrator in public schema will
--      automatically grant SELECT, INSERT, UPDATE (tables) and USAGE, SELECT (sequences)
--      to pgms_app without requiring manual GRANTs after each migration.
--    - CRITICAL: This script does NOT execute DDL against specific application tables
--      (e.g., owners, garages) as they do not exist prior to Alembic migrations.
-- ==============================================================================

-- 1. Create Roles Idempotently
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pgms_migrator') THEN
        CREATE ROLE pgms_migrator WITH LOGIN CREATEDB BYPASSRLS NOSUPERUSER PASSWORD 'PgMigratorSecurePass2026x';
    ELSE
        ALTER ROLE pgms_migrator WITH LOGIN CREATEDB BYPASSRLS NOSUPERUSER;
    END IF;

    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pgms_app') THEN
        CREATE ROLE pgms_app WITH LOGIN NOBYPASSRLS NOSUPERUSER PASSWORD 'PgAppSecurePass2026x';
    ELSE
        ALTER ROLE pgms_app WITH LOGIN NOBYPASSRLS NOSUPERUSER;
    END IF;
END
$$;

-- 2. Grant Schema Access
GRANT USAGE, CREATE ON SCHEMA public TO pgms_migrator;
GRANT USAGE ON SCHEMA public TO pgms_app;

-- 3. Configure Default Privileges for Future Objects Created by pgms_migrator
ALTER DEFAULT PRIVILEGES FOR ROLE pgms_migrator IN SCHEMA public 
    GRANT SELECT, INSERT, UPDATE ON TABLES TO pgms_app;

ALTER DEFAULT PRIVILEGES FOR ROLE pgms_migrator IN SCHEMA public 
    GRANT USAGE, SELECT ON SEQUENCES TO pgms_app;
