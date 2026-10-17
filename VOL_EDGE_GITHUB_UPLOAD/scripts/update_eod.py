from __future__ import annotations
import argparse
from datetime import date
from vol_edge.ingest import import_nse_day

p=argparse.ArgumentParser(description="Download and import NSE CM/FO bhavcopies for one date")
p.add_argument("trade_date", type=date.fromisoformat, help="Trading date YYYY-MM-DD")
p.add_argument("--db", default="data/vol_edge.sqlite3")
a=p.parse_args()
print(import_nse_day(a.trade_date,a.db))
