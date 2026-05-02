#!/bin/bash
# Runs on every PostgreSQL container start via docker-compose command override.
# Drops and recreates the mediatech database so the pipeline always starts clean.
set -e

echo "Resetting mediatech database..."

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "postgres" <<-EOSQL
    DROP DATABASE IF EXISTS mediatech;
    CREATE DATABASE mediatech;
EOSQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "mediatech" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS unaccent;
    CREATE EXTENSION IF NOT EXISTS vector;
EOSQL

echo "mediatech database reset complete."
