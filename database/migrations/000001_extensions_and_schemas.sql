-- Chic A Boo — extensions and schemas
-- PostgreSQL 15 (Supabase)

CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS admin;

COMMENT ON SCHEMA auth IS 'Authentication credentials, sessions, tokens, security events';
COMMENT ON SCHEMA admin IS 'Back-office users, RBAC, audit trail';
