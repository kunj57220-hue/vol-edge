"""Build a compact SQLite seed without modifying the full local archive."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vol_edge.compact import compact_cash_rows, compact_option_rows
from vol_edge.db import connect


def build(source: str, target: str) -> dict:
    src = sqlite3.connect(source)
    dst = connect(target)
    try:
        dates = [r[0] for r in src.execute("SELECT DISTINCT trade_date FROM cash_eod ORDER BY trade_date")]
        src_count = src.execute("SELECT COUNT(*) FROM fo_eod").fetchone()[0]
        cash_count = option_count = 0
        with dst:
            for day in dates:
                cash = src.execute("SELECT * FROM cash_eod WHERE trade_date=?", (day,)).fetchall()
                compact_cash = compact_cash_rows(cash)
                if compact_cash:
                    dst.executemany("INSERT INTO cash_eod VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(trade_date,symbol,series) DO UPDATE SET close=excluded.close",
                                    compact_cash)
                    cash_count += len(compact_cash)
                spots = {r[1]: r[6] for r in compact_cash if r[6] and r[6] > 0}
                fo = src.execute("SELECT * FROM fo_eod WHERE trade_date=?", (day,)).fetchall()
                compact_fo = compact_option_rows(fo, spots)
                if compact_fo:
                    dst.executemany("INSERT INTO fo_eod VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(trade_date,instrument,symbol,expiry,strike,option_type) DO UPDATE SET close=excluded.close,contracts=excluded.contracts,lot_size=excluded.lot_size",
                                    compact_fo)
                    option_count += len(compact_fo)
                files = src.execute("SELECT * FROM bhavcopy_files WHERE trade_date=?", (day,)).fetchall()
                if files:
                    dst.executemany("INSERT INTO bhavcopy_files(trade_date,segment,source,file_sha256,imported_at,row_count) VALUES(?,?,?,?,?,?) ON CONFLICT(trade_date,segment) DO UPDATE SET source=excluded.source,file_sha256=excluded.file_sha256,imported_at=excluded.imported_at,row_count=excluded.row_count",
                                    files)
        return {"sessions": len(dates), "cash_rows": cash_count, "full_fo_rows": src_count,
                "compact_fo_rows": option_count, "fo_reduction_pct": round(100*(1-option_count/src_count),2) if src_count else 0}
    finally:
        src.close()
        dst.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Create compact cloud seed from the full local SQLite archive")
    parser.add_argument("--source", default="data/vol_edge.sqlite3")
    parser.add_argument("--output", default="data/vol_edge_compact.sqlite3")
    args = parser.parse_args()
    print(build(args.source, args.output))
    print(f"Compact seed: {Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
