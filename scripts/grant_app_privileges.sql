-- ==============================================================================
-- PGMS SaaS - Phase 5: Hardened Post-Migration Privileges Grant for pgms_app
-- Reference: docs/specs/phase-5-saas/spec.md (Section 4.1)
-- Task: 2.6 in docs/specs/phase-5-saas/tasks.md
-- ==============================================================================
--
-- SECURITY ARCHITECTURE & PRIVILEGE ENFORCEMENT:
-- ----------------------------------------------
-- 1. Day-to-Day Application Role (pgms_app):
--    - Granted SELECT, INSERT, UPDATE across all public tables and USAGE, SELECT on sequences.
--    - Enables regular CRUD operations for active sessions, shifts, pricing, subscriptions.
--
-- 2. Audit Trail Immutability (B2):
--    - UPDATE, DELETE, and TRUNCATE are explicitly REVOKED on 'audit_logs'.
--    - Audit logs are append-only; records can never be modified or expunged by the application.
--
-- 3. Core Tenant Protection (B2):
--    - DELETE and TRUNCATE are REVOKED on 'owners', 'garages', and 'users'.
--    - Prevents accidental cascading deletion of whole tenant trees from the application layer.
--
-- 4. DDL Prevention:
--    - CREATE on schema public is revoked from pgms_app.
--    - pgms_app cannot execute any DDL (CREATE, ALTER, DROP) on tables or schemas.
-- ==============================================================================

-- 1. Grant Schema & Sequence Usage
GRANT USAGE ON SCHEMA public TO pgms_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO pgms_app;

-- 2. Grant Standard Operational Privileges on All Current Tables
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO pgms_app;

-- 3. Enforce Immutability on Audit Logs
REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM pgms_app;

-- 4. Guard Platform & Tenant Master Tables from Deletion/Truncation
REVOKE DELETE, TRUNCATE ON owners, garages, users FROM pgms_app;

-- 5. Revoke Schema-level Object Creation Privileges
REVOKE CREATE ON SCHEMA public FROM pgms_app;
