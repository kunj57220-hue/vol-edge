"""Independent scheduled EOD updater; does not import or start Streamlit."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vol_edge.ingest import update_history


def main() -> int:
    parser = argparse.ArgumentParser(description="Resume NSE CM/FO EOD updates independently of Streamlit")
    parser.add_argument("--db", default=os.getenv("VOL_EDGE_DATABASE_URL", "data/vol_edge.sqlite3"), help="Persistent database path or PostgreSQL URL")
    parser.add_argument("--sessions", type=int, default=90, help="Target number of paired sessions to retain")
    args = parser.parse_args()
    report = update_history(db_path=args.db, sessions=args.sessions)
    print(json.dumps(report, indent=2, default=str))
    return 2 if report["blocked"] or report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
