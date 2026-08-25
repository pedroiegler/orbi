-- Role da aplicacao: sem BYPASSRLS. E o unico papel usado pelo Runtime.
-- A senha de desenvolvimento vale apenas para o compose local.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'orbi_app') THEN
        CREATE ROLE orbi_app LOGIN PASSWORD 'orbi-dev' NOBYPASSRLS;
    END IF;
END
$$;

GRANT CONNECT ON DATABASE orbi TO orbi_app;
GRANT USAGE ON SCHEMA public TO orbi_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO orbi_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO orbi_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO orbi_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO orbi_app;
