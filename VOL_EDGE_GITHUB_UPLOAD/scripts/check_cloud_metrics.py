"""Recalculate the latest screen from compact cloud history after each updater run."""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT)); sys.path.insert(0,str(ROOT/'scripts'))
from vol_edge.db import connect, database_size_bytes
from validate_compact_snapshot import metric

def main():
    argparse.ArgumentParser(description="Recalculate dashboard metrics from compact PostgreSQL history").parse_args()
    dsn=os.getenv('VOL_EDGE_DATABASE_URL')
    if not dsn: raise SystemExit('VOL_EDGE_DATABASE_URL is required')
    cfg=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
    conn=connect(dsn)
    try:
        asof=conn.execute("SELECT MAX(trade_date) FROM cash_eod WHERE trade_date IN (SELECT trade_date FROM fo_eod)").fetchone()[0]
        if not asof: raise SystemExit('No paired CM/FO session is stored in Supabase yet.')
        symbols=[r[0] for r in conn.execute("SELECT DISTINCT symbol FROM fo_eod WHERE trade_date=? AND instrument IN ('OPTSTK','STO') ORDER BY symbol",(asof,))]
        results=[metric(conn,s,asof,cfg) for s in symbols]
        valid=sum(r['valid'] for r in results)
        size=database_size_bytes(conn)
        print(json.dumps({'asof':asof,'symbols':len(symbols),'valid_buy_ratios':valid,
            'invalid_buy_ratios':len(symbols)-valid,'database_size_bytes':size,
            'size_limit_bytes':500_000_000,'configured_holding_days':cfg.get('default_holding_days',5),
            'status':'ok' if valid else 'no valid ratios'},indent=2))
        return 0 if valid else 1
    finally: conn.close()
if __name__=='__main__': raise SystemExit(main())
