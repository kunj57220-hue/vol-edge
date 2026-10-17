"""Database connections for local SQLite and hosted Supabase PostgreSQL."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

SQLITE_SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS bhavcopy_files (
  trade_date TEXT NOT NULL, segment TEXT NOT NULL CHECK(segment IN ('CM','FO')),
  source TEXT NOT NULL, file_sha256 TEXT NOT NULL,
  imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, row_count INTEGER NOT NULL,
  PRIMARY KEY(trade_date, segment));
CREATE TABLE IF NOT EXISTS cash_eod (
  trade_date TEXT NOT NULL, symbol TEXT NOT NULL, series TEXT,
  open REAL, high REAL, low REAL, close REAL, last REAL, prev_close REAL,
  volume INTEGER, turnover REAL, trades INTEGER, isin TEXT,
  PRIMARY KEY(trade_date,symbol,series));
CREATE TABLE IF NOT EXISTS fo_eod (
  trade_date TEXT NOT NULL, instrument TEXT NOT NULL, symbol TEXT NOT NULL,
  expiry TEXT, strike REAL NOT NULL DEFAULT 0, option_type TEXT NOT NULL DEFAULT '',
  open REAL, high REAL, low REAL, close REAL, settle REAL,
  contracts INTEGER, turnover REAL, open_interest INTEGER, change_oi INTEGER,
  lot_size INTEGER,
  PRIMARY KEY(trade_date,instrument,symbol,expiry,strike,option_type));
CREATE TABLE IF NOT EXISTS data_warnings (
  trade_date TEXT NOT NULL, symbol TEXT NOT NULL, warning TEXT NOT NULL,
  PRIMARY KEY(trade_date,symbol,warning));
CREATE INDEX IF NOT EXISTS ix_cash_symbol_date ON cash_eod(symbol,trade_date);
CREATE INDEX IF NOT EXISTS ix_fo_symbol_date ON fo_eod(symbol,trade_date);
CREATE INDEX IF NOT EXISTS ix_fo_expiry ON fo_eod(symbol,expiry,trade_date);
"""
# Backward-compatible schema name used by the in-memory SQLite unit tests.
SCHEMA = SQLITE_SCHEMA

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS bhavcopy_files (
  trade_date TEXT NOT NULL, segment TEXT NOT NULL CHECK(segment IN ('CM','FO')),
  source TEXT NOT NULL, file_sha256 TEXT NOT NULL,
  imported_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, row_count BIGINT NOT NULL,
  PRIMARY KEY(trade_date, segment));
CREATE TABLE IF NOT EXISTS cash_eod (
  trade_date TEXT NOT NULL, symbol TEXT NOT NULL, series TEXT,
  open DOUBLE PRECISION, high DOUBLE PRECISION, low DOUBLE PRECISION,
  close DOUBLE PRECISION, last DOUBLE PRECISION, prev_close DOUBLE PRECISION,
  volume BIGINT, turnover DOUBLE PRECISION, trades BIGINT, isin TEXT,
  PRIMARY KEY(trade_date,symbol,series));
CREATE TABLE IF NOT EXISTS fo_eod (
  trade_date TEXT NOT NULL, instrument TEXT NOT NULL, symbol TEXT NOT NULL,
  expiry TEXT, strike DOUBLE PRECISION NOT NULL DEFAULT 0, option_type TEXT NOT NULL DEFAULT '',
  open DOUBLE PRECISION, high DOUBLE PRECISION, low DOUBLE PRECISION,
  close DOUBLE PRECISION, settle DOUBLE PRECISION,
  contracts BIGINT, turnover DOUBLE PRECISION, open_interest BIGINT,
  change_oi BIGINT, lot_size BIGINT,
  PRIMARY KEY(trade_date,instrument,symbol,expiry,strike,option_type));
CREATE TABLE IF NOT EXISTS data_warnings (
  trade_date TEXT NOT NULL, symbol TEXT NOT NULL, warning TEXT NOT NULL,
  PRIMARY KEY(trade_date,symbol,warning));
CREATE INDEX IF NOT EXISTS ix_cash_symbol_date ON cash_eod(symbol,trade_date);
CREATE INDEX IF NOT EXISTS ix_fo_symbol_date ON fo_eod(symbol,trade_date);
CREATE INDEX IF NOT EXISTS ix_fo_expiry ON fo_eod(symbol,expiry,trade_date);
"""


class PostgresConnection:
    """Small DB-API shim so existing qmark SQL and tuple rows work with psycopg."""
    def __init__(self, dsn: str):
        try:
            import psycopg
            from psycopg.rows import tuple_row
        except ImportError as exc:
            raise RuntimeError("PostgreSQL support requires psycopg[binary]; install requirements.txt") from exc
        if "sslmode=" not in dsn:
            dsn += ("&" if "?" in dsn else "?") + "sslmode=require"
        self._conn = psycopg.connect(dsn, row_factory=tuple_row, connect_timeout=15)

    @staticmethod
    def _sql(statement: str) -> str:
        return statement.replace("?", "%s")

    def execute(self, statement: str, parameters=()):
        return self._conn.execute(self._sql(statement), parameters)

    def executemany(self, statement: str, parameters):
        cursor = self._conn.cursor()
        cursor.executemany(self._sql(statement), parameters)
        return cursor

    def executescript(self, script: str):
        for statement in script.split(";"):
            if statement.strip():
                self._conn.execute(statement)

    def commit(self): self._conn.commit()
    def rollback(self): self._conn.rollback()
    def close(self): self._conn.close()
    def __enter__(self):
        self._conn.__enter__()
        return self
    def __exit__(self, exc_type, exc_value, traceback):
        return self._conn.__exit__(exc_type, exc_value, traceback)


def is_postgres(target: str | Path | None = None) -> bool:
    value = str(target or os.getenv("VOL_EDGE_DATABASE_URL", ""))
    return value.startswith(("postgres://", "postgresql://"))


def connect(path: str | Path = "data/vol_edge.sqlite3"):
    target = str(path)
    if is_postgres(target):
        conn = PostgresConnection(target)
        conn.executescript(POSTGRES_SCHEMA)
        conn.commit()
        return conn
    if target.startswith("sqlite:///"):
        target = target.removeprefix("sqlite:///")
    local = Path(target)
    local.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(local)
    conn.executescript(SQLITE_SCHEMA)
    return conn


def database_size_bytes(conn) -> int | None:
    """Return allocated DB bytes for quota monitoring (PostgreSQL only)."""
    if isinstance(conn, PostgresConnection):
        return int(conn.execute("SELECT pg_database_size(current_database())").fetchone()[0])
    return None
