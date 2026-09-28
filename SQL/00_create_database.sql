-- =====================================================================
-- One-time setup (run as a superuser, connected to the "postgres" database).
-- The loader (Python/02_load_postgres.py) runs this automatically on first use.
--
-- The database gets its own tablespace on the D: drive. Change LOCATION to any
-- empty folder the PostgreSQL service account can write to
-- (on Windows: NT AUTHORITY\NetworkService).
-- =====================================================================

CREATE TABLESPACE hospital_finance_ts LOCATION 'D:/PostgresData/hospital_finance';

CREATE DATABASE hospital_finance
    WITH TABLESPACE = hospital_finance_ts
         ENCODING = 'UTF8'
         TEMPLATE = template0;

COMMENT ON DATABASE hospital_finance IS
    'Financial performance of US hospitals from CMS cost reports, 2011-2023 (portfolio project)';
