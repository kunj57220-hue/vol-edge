"""Verify a seeded Neon database against the compact local snapshot."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vol_edge.db import connect, database_size_bytes  # noqa: E402
from validate_compact_snapshot import metric  # noqa: E402


def stats(conn, table: str) -> dict:
    row = conn.execute(
        f"SELECT COUNT(*), COUNT(DISTINCT trade_date), MIN(trade_date), MAX(trade_date) FROM {table}"
    ).fetchone()
    return {"rows": int(row[0]), "sessions": int(row[1]), "first": row[2], "last": row[3]}


def main() -> int:
    dsn = os.getenv("VOL_EDGE_DATABASE_URL")
    if not dsn or not dsn.startswith(("postgres://", "postgresql://")):
        raise SystemExit("Set VOL_EDGE_DATABASE_URL in the environment before running.")
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    local = sqlite3.connect(ROOT / "data" / "vol_edge_compact.sqlite3")
    cloud = connect(dsn)
    try:
        asof = local.execute(
            "SELECT MAX(trade_date) FROM cash_eod WHERE trade_date IN "
            "(SELECT trade_date FROM fo_eod)"
        ).fetchone()[0]
        symbols = [r[0] for r in local.execute(
            "SELECT DISTINCT symbol FROM fo_eod WHERE trade_date=? "
            "AND instrument IN ('OPTSTK','STO') ORDER BY symbol", (asof,)
        )]
        local_eq = local.execute(
            "SELECT COUNT(DISTINCT symbol) FROM cash_eod WHERE trade_date=? AND series IN ('EQ','')",
            (asof,),
        ).fetchone()[0]
        cloud_eq = cloud.execute(
            "SELECT COUNT(DISTINCT symbol) FROM cash_eod WHERE trade_date=? AND series IN ('EQ','')",
            (asof,),
        ).fetchone()[0]
        local_fo = len(symbols)
        cloud_fo = cloud.execute(
            "SELECT COUNT(DISTINCT symbol) FROM fo_eod WHERE trade_date=? "
            "AND instrument IN ('OPTSTK','STO')", (asof,),
        ).fetchone()[0]

        valid = 0
        mismatches = []
        max_diff: dict[str, float] = {}
        for symbol in symbols:
            expected = metric(local, symbol, asof, cfg)
            actual = metric(cloud, symbol, asof, cfg)
            if expected["valid"]:
                valid += 1
            if expected["valid"] != actual["valid"]:
                mismatches.append({"symbol": symbol, "field": "valid", "local": expected["valid"], "cloud": actual["valid"]})
                continue
            if expected["valid"]:
                for field in ("IV", "Fair IV", "HV5", "HV20", "Theta/Vega", "Downside IV", "Buy Ratio"):
                    difference = abs(expected[field] - actual[field])
                    max_diff[field] = max(max_diff.get(field, 0.0), difference)
                    if difference > 1e-10:
                        mismatches.append({"symbol": symbol, "field": field, "local": expected[field], "cloud": actual[field], "difference": difference})

        local_stats = {table: stats(local, table) for table in ("cash_eod", "fo_eod", "bhavcopy_files")}
        cloud_stats = {table: stats(cloud, table) for table in ("cash_eod", "fo_eod", "bhavcopy_files")}
        table_mismatches = {
            table: {"local": local_stats[table], "cloud": cloud_stats[table]}
            for table in local_stats if local_stats[table] != cloud_stats[table]
        }
        result = {
            "asof": asof,
            "local": local_stats,
            "cloud": {**cloud_stats, "allocated_database_bytes": database_size_bytes(cloud)},
            "latest_date_coverage": {"local_cash_EQ_symbols": int(local_eq), "cloud_cash_EQ_symbols": int(cloud_eq),
                                     "local_FO_symbols": int(local_fo), "cloud_FO_symbols": int(cloud_fo)},
            "local_valid_buy_ratios": valid,
            "mismatches": mismatches,
            "table_count_or_date_mismatches": table_mismatches,
            "max_abs_difference": max_diff,
        }
        print(json.dumps(result, indent=2))
        return 1 if mismatches or table_mismatches or local_eq != cloud_eq or local_fo != cloud_fo else 0
    finally:
        local.close()
        cloud.close()


if __name__ == "__main__":
    raise SystemExit(main())
