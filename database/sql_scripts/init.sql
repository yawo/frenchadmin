-- Runs once on first volume creation.
-- Creates the airflow database; mediatech is handled by reset_mediatech.sh on every start.
SELECT 'CREATE DATABASE airflow'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'airflow')\gexec
