"""One-time seed of Supabase from a compact local snapshot; the full archive is read-only."""
from __future__ import annotations
import argparse, os, sqlite3, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from vol_edge.db import connect, database_size_bytes

def batches(cursor, size=3000):
    while rows:=cursor.fetchmany(size):
        yield rows

def main():
    p=argparse.ArgumentParser(description="Seed compact VOL EDGE tables to Supabase PostgreSQL")
    p.add_argument('--source',default='data/vol_edge_compact.sqlite3')
    args=p.parse_args()
    dsn=os.getenv('VOL_EDGE_DATABASE_URL')
    if not dsn or not dsn.startswith(('postgres://','postgresql://')):
        raise SystemExit('Set VOL_EDGE_DATABASE_URL to the Supabase PostgreSQL connection string before running.')
    source=sqlite3.connect(args.source); target=connect(dsn)
    try:
        exists=sum(target.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('cash_eod','fo_eod','bhavcopy_files'))
        if exists:
            raise SystemExit('Target database is not empty; refusing to overwrite existing data.')
        with target:
            for table, cols in (
                ('cash_eod','trade_date,symbol,series,open,high,low,close,last,prev_close,volume,turnover,trades,isin'),
                ('fo_eod','trade_date,instrument,symbol,expiry,strike,option_type,open,high,low,close,settle,contracts,turnover,open_interest,change_oi,lot_size'),
                ('bhavcopy_files','trade_date,segment,source,file_sha256,imported_at,row_count')):
                cur=source.execute(f'SELECT * FROM {table}')
                n=0; marks=','.join(['?']*len(cols.split(',')))
                for batch in batches(cur):
                    target.executemany(f'INSERT INTO {table}({cols}) VALUES ({marks}) ON CONFLICT DO NOTHING',batch)
                    n+=len(batch)
                print(f'Seeded {table}: {n:,} compact rows')
            target.execute('ANALYZE cash_eod'); target.execute('ANALYZE fo_eod')
        size=database_size_bytes(target)
        print(f'Supabase database allocated: {size/1_000_000:.1f} MB / 500 MB')
        if size>=350_000_000: print('WARNING: storage is at or above 70% of the Free tier; reduce retention before continuing.')
    finally:
        source.close(); target.close()
if __name__=='__main__': main()
