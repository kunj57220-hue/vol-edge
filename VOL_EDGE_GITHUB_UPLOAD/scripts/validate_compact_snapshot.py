"""Compare local-full and cloud-compact dashboard calculations symbol by symbol."""
from __future__ import annotations
import argparse, json, sqlite3, sys
from datetime import date
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from vol_edge.buy_ratio import downside_references, ewma, volatility_buy_ratio
from vol_edge.market_data import historical_atm_iv_series
from vol_edge.volatility import realized_volatility, implied_volatility, greeks

def metric(conn, symbol, asof, cfg=None):
    cfg=cfg or {}; rate=float(cfg.get('risk_free_rate',.06)); dividend=float(cfg.get('dividend_yield',0))
    holding=int(cfg.get('default_holding_days',5)); fair_n=int(cfg.get('fair_iv_lookback',10))
    fair_decay=float(cfg.get('fair_iv_decay',.8)); downside_n=int(cfg.get('downside_iv_lookback',50))
    data=conn.execute("SELECT trade_date,close FROM cash_eod WHERE symbol=? AND series IN ('EQ','') AND trade_date<=? ORDER BY trade_date DESC LIMIT 253",(symbol,asof)).fetchall()
    closes=[r[1] for r in reversed(data) if r[1] is not None]
    spot=closes[-1] if closes else None
    chain=conn.execute("SELECT expiry,strike,option_type,close,contracts FROM fo_eod WHERE symbol=? AND trade_date=? AND instrument IN ('OPTSTK','STO') AND expiry>=? AND close>0 ORDER BY expiry,strike",(symbol,asof,asof)).fetchall()
    expiries=sorted({r[0] for r in chain if r[0]})
    if not spot or not expiries: return {"valid":False}
    expiry=expiries[0]; dte=(date.fromisoformat(expiry)-date.fromisoformat(asof)).days; by={}
    for e,k,t,px,vol in chain:
        if e==expiry and t in ('CE','PE','C','P'): by.setdefault(k,{})[t]=(px,vol)
    strikes=[k for k,v in by.items() if ('CE' in v or 'C' in v) and ('PE' in v or 'P' in v)]
    if not strikes or dte<=0: return {"valid":False}
    strike=min(strikes,key=lambda k:abs(k-spot)); legs=by[strike]; call=legs.get('CE',legs.get('C')); put=legs.get('PE',legs.get('P'))
    if not call[1] or not put[1]: return {"valid":False}
    ci=implied_volatility(call[0],spot,strike,dte/365,rate,'C',dividend); pi=implied_volatility(put[0],spot,strike,dte/365,rate,'P',dividend)
    iv=(ci+pi)/2 if ci is not None and pi is not None else None
    if iv is None:return {"valid":False}
    hist,_,_,_=historical_atm_iv_series(conn,symbol,asof,dte,90,rate,dividend)
    prior=closes[:-1]; rv=[v for end in range(21,len(prior)+1) if (v:=realized_volatility(prior[:end],20)) is not None]
    fair=ewma(rv[-fair_n:],fair_n,fair_decay) if len(rv)>=fair_n else None
    refs=downside_references(hist,downside_n); down=refs['minimum']
    cg=greeks(spot,strike,dte/365,rate,ci,'C',dividend); pg=greeks(spot,strike,dte/365,rate,pi,'P',dividend)
    theta=cg['theta_per_day']+pg['theta_per_day']; vega=cg['vega_per_vol_point']+pg['vega_per_vol_point']
    expiry_horizon=max(1,int(dte*252/365))
    ratio=None
    if len(hist)>=20 and holding<expiry_horizon:
        ratio=volatility_buy_ratio(iv,fair,theta,vega,down,holding,float(cfg.get('buy_ratio_denominator_floor_iv_points',1e-6)))['buy_ratio']
    return {"valid":ratio is not None,"IV":iv,"Fair IV":fair,"HV5":realized_volatility(closes,5),
            "HV20":realized_volatility(closes,20),"Theta/Vega":abs(theta)/vega,
            "Downside IV":down,"Buy Ratio":ratio}

def main():
    p=argparse.ArgumentParser(); p.add_argument('--full',default='data/vol_edge.sqlite3'); p.add_argument('--compact',default='data/vol_edge_compact.sqlite3'); a=p.parse_args()
    full=sqlite3.connect(a.full); compact=sqlite3.connect(a.compact); cfg=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
    asof=full.execute("SELECT MAX(trade_date) FROM cash_eod WHERE trade_date IN (SELECT trade_date FROM fo_eod)").fetchone()[0]
    syms=[r[0] for r in full.execute("SELECT DISTINCT symbol FROM fo_eod WHERE trade_date=? AND instrument IN ('OPTSTK','STO') ORDER BY symbol",(asof,))]
    mismatches=[]; valid=0; maxdiff={}
    for s in syms:
        x=metric(full,s,asof,cfg); y=metric(compact,s,asof,cfg)
        if x['valid']: valid+=1
        if x['valid']!=y['valid']:
            mismatches.append({"symbol":s,"full_valid":x['valid'],"compact_valid":y['valid']}); continue
        if x['valid']:
            for k in x:
                if k=='valid': continue
                diff=abs(x[k]-y[k]); maxdiff[k]=max(maxdiff.get(k,0),diff)
                if diff>1e-10: mismatches.append({"symbol":s,"field":k,"full":x[k],"compact":y[k],"difference":diff})
    print(json.dumps({"asof":asof,"symbols":len(syms),"valid_ratios":valid,"mismatches":mismatches,"max_abs_difference":maxdiff},indent=2))
    full.close(); compact.close()
    return 1 if mismatches else 0
if __name__=='__main__': raise SystemExit(main())
