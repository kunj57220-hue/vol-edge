"""Resumably seed the compact VOL EDGE snapshot into hosted PostgreSQL.

Each batch is its own transaction. Re-running the same seed is safe: rows with
matching primary keys are reconciled to the authoritative compact snapshot.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vol_edge.db import connect, database_size_bytes  # noqa: E402

TABLES = (
    (
        "cash_eod",
        "trade_date,symbol,series,open,high,low,close,last,prev_close,volume,turnover,trades,isin",
        ("trade_date", "symbol", "series"),
    ),
    (
        "fo_eod",
        "trade_date,instrument,symbol,expiry,strike,option_type,open,high,low,close,settle,contracts,turnover,open_interest,change_oi,lot_size",
        ("trade_date", "instrument", "symbol", "expiry", "strike", "option_type"),
    ),
    (
        "bhavcopy_files",
        "trade_date,segment,source,file_sha256,imported_at,row_count",
        ("trade_date", "segment"),
    ),
)


def _upsert_sql(table: str, columns: str, keys: tuple[str, ...]) -> str:
    names = columns.split(",")
    marks = ",".join("?" for _ in names)
    assignments = ",".join(f"{col}=EXCLUDED.{col}" for col in names if col not in keys)
    conflict = ",".join(keys)
    return (
        f"INSERT INTO {table}({columns}) VALUES ({marks}) "
        f"ON CONFLICT({conflict}) DO UPDATE SET {assignments}"
    )


def seed_table(
    source: sqlite3.Connection,
    target,
    table: str,
    columns: str,
    keys: tuple[str, ...],
    batch_size: int = 2000,
    progress: Callable[[str], None] = print,
) -> int:
    """Upload a table in committed date-ordered batches and report progress."""
    dates = [row[0] for row in source.execute(
        f"SELECT DISTINCT trade_date FROM {table} ORDER BY trade_date"
    )]
    total = int(source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    uploaded = 0
    statement = _upsert_sql(table, columns, keys)
    for session_index, day in enumerate(dates, start=1):
        cursor = source.execute(
            f"SELECT {columns} FROM {table} WHERE trade_date=? ORDER BY {','.join(keys)}", (day,)
        )
        session_uploaded = 0
        while True:
            batch = cursor.fetchmany(batch_size)
            if not batch:
                break
            try:
                target.executemany(statement, batch)
                target.commit()
            except BaseException:
                # Roll back only the in-flight batch. Earlier batches remain
                # committed and will be harmlessly reconciled on the next run.
                target.rollback()
                raise
            uploaded += len(batch)
            session_uploaded += len(batch)
            progress(
                f"{table}: session {session_index}/{len(dates)} ({day}), "
                f"{session_uploaded:,} rows this session; {uploaded:,}/{total:,} rows processed"
            )
    if not dates:
        progress(f"{table}: empty in source")
    return uploaded


def main() -> int:
    parser = argparse.ArgumentParser(description="Resume seeding compact VOL EDGE tables to PostgreSQL")
    parser.add_argument("--source", default=str(ROOT / "data" / "vol_edge_compact.sqlite3"))
    parser.add_argument("--batch-size", type=int, default=2000, help="Rows per committed transaction (default: 2000)")
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")

    dsn = os.getenv("VOL_EDGE_DATABASE_URL")
    if not dsn or not dsn.startswith(("postgres://", "postgresql://")):
        raise SystemExit("Set VOL_EDGE_DATABASE_URL securely in the environment before running.")
    source_path = Path(args.source).resolve()
    if not source_path.is_file():
        raise SystemExit(f"Compact source database not found: {source_path}")

    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    target = None
    try:
        expected = {table: int(source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table, _, _ in TABLES}
        target = connect(dsn)

        # A partial seed is expected after interruption. Extra target records
        # indicate the database is not just a partial copy of this snapshot;
        # refuse to continue rather than delete or mask those records.
        for table, source_count in expected.items():
            current = int(target.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            if current > source_count:
                raise SystemExit(
                    f"Refusing to seed: target {table} has {current:,} rows, "
                    f"more than this snapshot's {source_count:,}. No rows were deleted."
                )
            print(f"{table}: {current:,}/{source_count:,} rows currently present; resuming", flush=True)

        for table, columns, keys in TABLES:
            seed_table(source, target, table, columns, keys, args.batch_size,
                       lambda message: print(message, flush=True))

        target.execute("ANALYZE cash_eod")
        target.execute("ANALYZE fo_eod")
        target.commit()
        print("Committed table statistics.", flush=True)

        counts = {table: int(target.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table, _, _ in TABLES}
        print("Final PostgreSQL row counts:", ", ".join(f"{table}={count:,}" for table, count in counts.items()), flush=True)
        mismatches = {table: (expected[table], counts[table]) for table in expected if expected[table] != counts[table]}
        if mismatches:
            raise RuntimeError(f"Final row counts do not match compact snapshot: {mismatches}")

        size = database_size_bytes(target)
        limit = int(os.getenv("VOL_EDGE_DB_SIZE_LIMIT_BYTES", "1000000000"))
        if size is not None:
            print(f"PostgreSQL database allocated: {size / 1_000_000:.1f} MB / {limit / 1_000_000:.0f} MB configured free-tier limit", flush=True)
            if size >= limit * .7:
                print("WARNING: storage is at or above 70% of the configured free-tier limit; review retention.", flush=True)
        return 0
    except KeyboardInterrupt:
        if target is not None:
            target.rollback()
        print("\nSeed interrupted. Completed batches are committed; rerun the same command to resume safely.", flush=True)
        return 130
    finally:
        source.close()
        if target is not None:
            target.close()


if __name__ == "__main__":
    raise SystemExit(main())
