"""Minimal Streamlit V1 screen; relies only on locally ingested observations."""
from __future__ import annotations
import json
import os
import hmac
import math
from pathlib import Path

try:
    import streamlit as st
except ImportError as exc:
    raise SystemExit("Install the dashboard dependency with: python -m pip install -r requirements.txt") from exc

from vol_edge.db import connect, is_postgres, database_size_bytes
from vol_edge.ingest import import_bhavcopy, update_history, download_status
from vol_edge.volatility import realized_volatility, realized_volatility_warnings, implied_volatility, percentile_rank, greeks
from vol_edge.buy_ratio import ewma, downside_references, volatility_buy_ratio
from vol_edge.market_data import historical_atm_iv_series
from datetime import date

st.set_page_config(page_title="Vol Edge", layout="wide")
st.title("Vol Edge · NSE Long Volatility")
st.caption("NSE end-of-day long-volatility screen. Missing or unreliable market observations remain blank with a warning.")
config=json.loads(Path("config.json").read_text(encoding="utf-8")) if Path("config.json").exists() else {}
def get_secret(name, default=""):
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default
database_target=os.getenv("VOL_EDGE_DATABASE_URL") or get_secret("database_url") or os.getenv("VOL_EDGE_DB",config.get("database","data/vol_edge.sqlite3"))
db=database_target if is_postgres(database_target) else Path(database_target)
cloud_mode=is_postgres(db)
rate=float(config.get("risk_free_rate",.06)); dividend=float(config.get("dividend_yield",0))
with st.sidebar:
    admin_required=os.getenv("VOL_EDGE_REQUIRE_ADMIN","1" if cloud_mode else "0")=="1"
    admin_token=os.getenv("VOL_EDGE_ADMIN_TOKEN") or get_secret("admin_token")
    if admin_required:
        typed_token=st.text_input("Admin access",type="password")
        is_admin=bool(admin_token) and hmac.compare_digest(typed_token,admin_token)
        if not admin_token: st.error("Admin refresh is disabled until VOL_EDGE_ADMIN_TOKEN is set on the host.")
        elif not is_admin: st.caption("Dashboard is read-only until the admin token is entered.")
    else:
        is_admin=True
        st.caption("Local mode: upload and update controls are enabled." if not cloud_mode else "Hosted database is read-only here; GitHub Actions updates it independently.")
    st.subheader("Import NSE bhavcopy")
    try:
        counts=download_status(db)
        st.caption(f"Files — completed: {counts['completed']} · failed: {counts['failed']} · pending toward 90 sessions: {counts['pending']}")
    except Exception as exc:
        counts={"completed":0,"failed":0,"pending":180}
        if cloud_mode:
            st.warning(f"Hosted database status is temporarily unavailable ({type(exc).__name__}); it may be waking from sleep. Refresh in a moment or check the PostgreSQL provider status.")
        else:
            st.error(f"Could not read import status: {exc}")
    trade_date=st.date_input("Trade date", value=date.today())
    segment=st.selectbox("Segment",["CM","FO"])
    upload=st.file_uploader("Upload official NSE CSV or ZIP",type=["csv","zip"],key=segment,disabled=not is_admin)
    if st.button("Import file",disabled=upload is None or not is_admin):
        try:
            n=import_bhavcopy(upload.getvalue(),segment,trade_date,f"upload:{upload.name}",db)
            st.success(f"Imported {n:,} {segment} rows")
            st.rerun()
        except Exception as exc:
            st.error(f"Import failed: {exc}")
    st.divider()
    update_clicked=st.button("Update Data",help="Resume missing CM+FO files toward 90 complete sessions",disabled=not is_admin)
    retry_clicked=st.button("Retry Failed Downloads",help="Retry dates recorded as failed in data/download_failures.log",disabled=not is_admin)
    if update_clicked or retry_clicked:
        bar=st.progress(0.0)
        status=st.empty()
        def show_progress(message,current,total):
            bar.progress(min(1.0,current/max(total,1)))
            status.caption(message)
        try:
            with st.spinner("Downloading official NSE bhavcopies in date order. This can take several minutes."):
                report=update_history(db_path=db,sessions=90,progress=show_progress,retry_failed=retry_clicked)
            st.session_state["last_update_report"]=report
            if report["blocked"]:
                st.error("NSE restricted or rate-limited access. Requests stopped; try later or use manual upload.")
            elif report["completed"]:
                st.success(f"Complete sessions: {report['completed']} / 90.")
            else:
                st.warning("No complete sessions were added. See update details below.")
        except Exception as exc:
            st.error(f"Data update stopped safely: {type(exc).__name__}: {exc}. Imported data was preserved; retry later.")
        st.rerun()
    if "last_update_report" in st.session_state:
        with st.expander("Last update details"):
            report=st.session_state["last_update_report"]
            st.write(f"Complete sessions: {report['completed']} / {report['target_sessions']}")
            st.write(f"Files completed: {report['completed_files']}; failed: {report['failed_files']}; pending: {report['pending_files']}")
            st.write(f"Downloaded this run: {len(report['downloaded'])}; imported this run: {len(report['imported'])}; already present: {len(report['skipped'])}")
            if report["missing"]: st.warning("\n".join(report["missing"][-30:]))
            if report["failed"]: st.error("\n".join(report["failed"][-30:]))
            if report["downloaded"]: st.caption("Downloaded: " + ", ".join(report["downloaded"][-30:]))
    # First launch triggers the requested initial 90-session backfill once.
    if not cloud_mode and os.getenv("VOL_EDGE_DISABLE_PAGE_BACKFILL","0")!="1" and "initial_backfill_checked" not in st.session_state:
        st.session_state["initial_backfill_checked"]=True
        conn0=connect(db)
        has_data=conn0.execute("SELECT 1 FROM bhavcopy_files LIMIT 1").fetchone() is not None
        conn0.close()
        if not has_data:
            st.info("No imported NSE sessions found. Starting the initial 90-session backfill.")
            st.session_state["run_initial_backfill"]=True
    if st.session_state.pop("run_initial_backfill",False) and not admin_required:
        bar=st.progress(0.0); status=st.empty()
        def show_initial(message,current,total):
            bar.progress(min(1.0,current/max(total,1))); status.caption(message)
        try:
            with st.spinner("Initial NSE data backfill is running. You can use manual upload if NSE access is unavailable."):
                report=update_history(db_path=db,sessions=90,progress=show_initial)
            st.session_state["last_update_report"]=report
        except Exception as exc:
            st.error(f"Initial data update stopped safely: {type(exc).__name__}: {exc}. Imported data was preserved.")
        st.rerun()
if not cloud_mode and not db.exists():
    st.info("Import official NSE CM and FO files before screening.")
    st.stop()

with st.sidebar:
    st.subheader("Buy Ratio assumptions")
    holding_days = st.number_input("Holding period (trading days)", min_value=1, max_value=30, value=int(config.get("default_holding_days",5)))
    fair_lookback = st.number_input("Fair IV lookback (sessions)", min_value=2, max_value=50, value=int(config.get("fair_iv_lookback",10)))
    fair_method = st.selectbox("Fair IV method", ["EWMA", "Simple average"])
    downside_lookback = st.number_input("Downside IV lookback (sessions)", min_value=10, max_value=90, value=int(config.get("downside_iv_lookback",50)))
    downside_reference = st.selectbox("Downside IV reference", ["Historical minimum", "Historical median", "Fair IV"])
    st.caption("Per-stock holding periods can be adjusted in the stock detail panel.")

try:
    conn=connect(db)
except Exception as exc:
    if cloud_mode:
        st.error(f"The hosted database is unavailable ({type(exc).__name__}). The PostgreSQL service may be waking from sleep or connection settings may need attention. Refresh in a moment; the scheduled updater runs independently.")
        st.stop()
    raise
storage_bytes=database_size_bytes(conn)
if storage_bytes is not None:
    storage_limit=int(os.getenv("VOL_EDGE_DB_SIZE_LIMIT_BYTES", "1000000000"))
    storage_percent=100*storage_bytes/storage_limit
    storage_limit_mb=storage_limit/1024/1024
    st.sidebar.caption(f"PostgreSQL storage: {storage_bytes/1024/1024:.1f} / {storage_limit_mb:.0f} MB configured Free-tier limit ({storage_percent:.0f}%).")
    if storage_percent>=90: st.sidebar.error("Database is near its configured Free storage limit. Reduce retained history before the next import.")
    elif storage_percent>=75: st.sidebar.warning("Database storage is approaching its configured Free-tier limit; review retention.")
    elif storage_percent>=60: st.sidebar.warning("Database storage passed 60% of its configured Free-tier limit; review growth and retention.")
dates=[r[0] for r in conn.execute("SELECT DISTINCT trade_date FROM cash_eod ORDER BY trade_date DESC LIMIT 253")]
if not dates:
    st.info("Cash-market history is empty."); st.stop()
latest_cash=dates[0]
latest_fo=conn.execute("SELECT MAX(trade_date) FROM fo_eod").fetchone()[0]
asof=conn.execute("SELECT MAX(trade_date) FROM cash_eod WHERE trade_date IN (SELECT trade_date FROM fo_eod)").fetchone()[0]
if not asof:
    st.info("No paired CM/FO session is available yet."); st.stop()
if latest_cash!=asof or latest_fo!=asof:
    st.warning(f"The latest CM/FO imports are not paired (CM {latest_cash}, FO {latest_fo}); calculations use the latest complete paired session, {asof}.")
age_days=(date.today()-date.fromisoformat(asof)).days
if age_days>4:
    st.warning(f"Potentially stale EOD data: latest complete paired session is {asof} ({age_days} calendar days ago). Update the data or confirm intervening NSE holidays.")
symbols=[r[0] for r in conn.execute("SELECT DISTINCT symbol FROM fo_eod WHERE trade_date=? AND instrument IN ('OPTSTK','STO') ORDER BY symbol",(asof,))]
query=st.text_input("Filter symbols").strip().upper()

@st.cache_data(ttl=300, show_spinner=False)
def cached_iv_history(db_path, db_mtime, symbol, asof_date, target_dte, lookback, risk_free, div_yield):
    history_conn=connect(db_path)
    try:
        return historical_atm_iv_series(history_conn,symbol,asof_date,target_dte,lookback,risk_free,div_yield)
    finally:
        history_conn.close()

db_revision=(str(conn.execute("SELECT MAX(imported_at) FROM bhavcopy_files").fetchone()[0]) if cloud_mode else str(db.stat().st_mtime))

rows=[]
result_by_symbol={}
for symbol in symbols:
    data=conn.execute("SELECT trade_date,close FROM cash_eod WHERE symbol=? AND series IN ('EQ','') AND trade_date<=? ORDER BY trade_date DESC LIMIT 253",(symbol,asof)).fetchall()
    closes=[r[1] for r in reversed(data) if r[1] is not None]
    close_by_date={d:p for d,p in data if p is not None}
    hv5=realized_volatility(closes,5); hv20=realized_volatility(closes,20)
    spot=closes[-1] if closes else None
    chain=conn.execute("SELECT expiry,strike,option_type,close,contracts,lot_size FROM fo_eod WHERE symbol=? AND trade_date=? AND instrument IN ('OPTSTK','STO') AND expiry>=? AND close>0 ORDER BY expiry,strike",(symbol,asof,asof)).fetchall()
    price_observations=[(d,p) for d,p in data if p is not None]
    warnings=realized_volatility_warnings(price_observations,hv5,hv20)
    recent_market_dates=set(dates[:20])
    missing_recent=sorted(recent_market_dates-set(close_by_date))
    if missing_recent:
        warnings.append(f"Missing CM closing prices on {len(missing_recent)} of the latest 20 market sessions; returns and realized volatility may bridge gaps.")
    result=None
    expiries=sorted({c[0] for c in chain if c[0]})
    if not spot: warnings.append("Spot price unavailable in imported CM data.")
    if not chain: warnings.append("No valid stock-option closes for the latest session.")
    if spot and expiries:
        expiry=expiries[0]
        days=(date.fromisoformat(expiry)-date.fromisoformat(asof)).days
        bystrike={}
        for e,k,t,px,volume,lot_size in chain:
            if e==expiry and t in ("CE","PE","C","P"):
                bystrike.setdefault(k,{})[t]=(px,volume,lot_size)
        candidates=[k for k,v in bystrike.items() if ("CE" in v or "C" in v) and ("PE" in v or "P" in v)]
        if days>0 and candidates:
            strike=min(candidates,key=lambda k:abs(k-spot)); pair=bystrike[strike]
            call=pair.get("CE",pair.get("C")); put=pair.get("PE",pair.get("P"))
            if not call[1] or not put[1]:
                warnings.append("ATM IV unreliable: both call and put need nonzero traded contracts.")
                iv=None
            else:
                civ=implied_volatility(call[0],spot,strike,days/365,rate,"C",dividend)
                piv=implied_volatility(put[0],spot,strike,days/365,rate,"P",dividend)
                iv=(civ+piv)/2 if civ is not None and piv is not None else None
            if cloud_mode:
                hist,matched,omitted,dte_mismatch=historical_atm_iv_series(conn,symbol,asof,days,90,rate,dividend)
            else:
                hist,matched,omitted,dte_mismatch=cached_iv_history(str(db),db_revision,symbol,asof,days,90,rate,dividend)
            # Fair IV is an estimate from realized stock volatility, independent of
            # the separately retained historical option-IV series above. Exclude
            # the as-of close so the forecast uses only completed prior sessions.
            prior_closes=closes[:-1]
            rv_history=[rv for end in range(21,len(prior_closes)+1)
                        if (rv:=realized_volatility(prior_closes[:end],20)) is not None]
            enough_fair_history=len(rv_history)>=int(fair_lookback)
            fair=(ewma(rv_history,int(fair_lookback),float(config.get("fair_iv_decay",0.8))) if fair_method=="EWMA" else sum(rv_history[-int(fair_lookback):])/int(fair_lookback)) if enough_fair_history else None
            recent_prices=sorted(price_observations)[-30:]
            recent_large=[(day,math.log(px/prev_px)) for (_,prev_px),(day,px)
                          in zip(recent_prices,recent_prices[1:]) if prev_px>0 and px>0
                          and abs(math.log(px/prev_px))>=0.10]
            if recent_large:
                warnings.append("Fair IV's EWMA includes overlapping 20-session RV windows containing recent large return(s): "
                                + ", ".join(f"{day} {move:+.1%}" for day,move in recent_large)
                                + ". These observations are retained; review the source event before relying on FV.")
            refs=downside_references(hist,int(downside_lookback))
            chosen={"Historical minimum":refs["minimum"],"Historical median":refs["median"],"Fair IV":fair}[downside_reference]
            call_iv=implied_volatility(call[0],spot,strike,days/365,rate,"C",dividend) if call[1] else None
            put_iv=implied_volatility(put[0],spot,strike,days/365,rate,"P",dividend) if put[1] else None
            if iv is not None:
                call_g=greeks(spot,strike,days/365,rate,call_iv or iv,"C",dividend)
                put_g=greeks(spot,strike,days/365,rate,put_iv or iv,"P",dividend)
                theta=call_g["theta_per_day"]+put_g["theta_per_day"]
                vega=call_g["vega_per_vol_point"]+put_g["vega_per_vol_point"]
                expiry_horizon=max(1,int(days*252/365))
                if len(hist)<20:
                    ratio={"buy_ratio":None,"warning":"Fewer than 20 comparable historical IV observations; downside reference is insufficient."}
                elif int(holding_days)>=expiry_horizon:
                    ratio={"buy_ratio":None,"warning":f"Holding period must be shorter than the estimated {expiry_horizon}-session time to expiry."}
                else:
                    ratio=volatility_buy_ratio(iv,fair,theta,vega,chosen,int(holding_days),float(config.get("buy_ratio_denominator_floor_iv_points",1e-6)))
            else:
                theta=vega=None; ratio={"buy_ratio":None,"warning":"Current ATM IV could not be inverted from bhavcopy closes."}
            percentile=percentile_rank(hist,iv) if iv is not None else None
            regime_percentile=percentile_rank(hist[-30:],iv) if iv is not None else None
            rv_trend=(hv5/hv20-1) if hv5 and hv20 else None
            movement_hurdle=iv/19 if iv else None
            move_frequency=(sum(abs(b/a-1)>=movement_hurdle for a,b in zip(closes[:-1],closes[1:]))/max(len(closes)-1,1)
                            if movement_hurdle is not None and len(closes)>1 else None)
            if not hv5: warnings.append("HV5 unavailable: insufficient CM closes.")
            if not hv20: warnings.append("HV20 unavailable: insufficient CM closes.")
            if len(hist)<int(downside_lookback):
                warnings.append(f"Only {len(hist)} DTE-comparable historical IV observations; selected downside reference may be unreliable.")
            if omitted: warnings.append(f"{omitted} historical sessions omitted because spot/options were missing or DTE could not be normalized.")
            if dte_mismatch: warnings.append(f"{dte_mismatch} sessions had expiries more than 25% or 5 days from target DTE {days} and were excluded to avoid term-structure mixing.")
            if fair is None: warnings.append(f"Fair IV unavailable: fewer than {int(fair_lookback)} prior-session 20-day realized-volatility observations from CM closes.")
            if percentile is None: warnings.append("IV percentile unavailable: requires at least 20 comparable sessions.")
            if regime_percentile is None: warnings.append("30-session IV regime unavailable: fewer than 20 comparable sessions.")
            if ratio["warning"]: warnings.append(ratio["warning"])
            result={"IV":iv,"Fair IV":fair,"IV/FV":iv/fair if iv and fair else None,"IV percentile":percentile,
                "HV5":hv5,"HV20":hv20,"RV trend":rv_trend,"Theta/Vega (IV pts/day)":ratio.get("theta_points_per_day"),
                "IV regime percentile (30s)":regime_percentile,"Movement hurdle frequency":move_frequency,
                "Holding days":int(holding_days),"IV expansion (pts)":ratio.get("iv_expansion_points"),
                "Theta retention (pts)":ratio.get("theta_points"),"Downside reference":chosen,
                "IV compression (pts)":ratio.get("iv_compression_points"),"Theta loss (pts)":ratio.get("theta_points"),
                "Buy Ratio (%)":ratio.get("buy_ratio"),"Minimum IV":refs["minimum"],"Median IV":refs["median"],
                "History count":len(hist),"Spot":spot,"Strike":strike,"Expiry":expiry,"DTE":days,
                "Theta":theta,"Vega":vega,"Warnings":"; ".join(warnings),"History":hist,"Close history":close_by_date}
    if result is None:
        warnings.append("Buy Ratio unavailable: current ATM option pair or spot data missing.")
        result={"IV":None,"Fair IV":None,"IV/FV":None,"IV percentile":None,"HV5":hv5,"HV20":hv20,
            "RV trend":None,"Theta/Vega (IV pts/day)":None,"Holding days":int(holding_days),
            "IV regime percentile (30s)":None,"Movement hurdle frequency":None,
            "IV expansion (pts)":None,"Theta retention (pts)":None,"Downside reference":None,
            "IV compression (pts)":None,"Theta loss (pts)":None,"Buy Ratio (%)":None,
            "Minimum IV":None,"Median IV":None,"History count":0,"Spot":spot,"Strike":None,
            "Expiry":None,"DTE":None,"Theta":None,"Vega":None,"Warnings":"; ".join(warnings),
            "History":[],"Close history":close_by_date}
    import_stamp=conn.execute("SELECT MAX(imported_at) FROM bhavcopy_files WHERE trade_date=?",(asof,)).fetchone()[0]
    rows.append({"Symbol":symbol,"Spot":result["Spot"],"Current IV (%)":result["IV"]*100 if result["IV"] else None,
        "Fair IV (%)":result["Fair IV"]*100 if result["Fair IV"] else None,"IV/FV":result["IV/FV"],
        "HV5 (%)":result["HV5"]*100 if result["HV5"] else None,"HV20 (%)":result["HV20"]*100 if result["HV20"] else None,
        "RV trend":result["RV trend"],"IV percentile":result["IV percentile"],
        "IV regime percentile (30s)":result["IV regime percentile (30s)"],
        "Move hurdle frequency":result["Movement hurdle frequency"],
        "Daily theta/vega (IV pts)":result["Theta/Vega (IV pts/day)"],"Holding days":result["Holding days"],
        "IV expansion (pts)":result["IV expansion (pts)"],"Theta retention (pts)":result["Theta retention (pts)"],
        "Downside IV (%)":result["Downside reference"]*100 if result["Downside reference"] else None,
        "IV compression (pts)":result["IV compression (pts)"],"Theta loss (pts)":result["Theta loss (pts)"],
        "Buy Ratio (%)":result["Buy Ratio (%)"],"Last updated":import_stamp,"Data warnings":result["Warnings"]})
    result["Symbol"]=symbol
    result["Last updated"]=import_stamp
    result["Fair method"]=fair_method
    result["Downside reference name"]=downside_reference
    result["Downside lookback"]=int(downside_lookback)
    result["Ratio inputs"]={"IV":result["IV"],"Fair IV":result["Fair IV"],"Downside IV":result["Downside reference"],
        "Theta/Vega":result["Theta/Vega (IV pts/day)"],"Holding days":int(holding_days),
        "Breakdown":{k:result.get(k) for k in ("IV expansion (pts)","Theta retention (pts)","IV compression (pts)","Theta loss (pts)")}}
    result_by_symbol[symbol]=result

rows.sort(key=lambda r:r["Buy Ratio (%)"] if r["Buy Ratio (%)"] is not None else float("-inf"),reverse=True)
st.caption(f"Latest complete paired NSE EOD session: {asof}. This is not live intraday data. Buy Ratio is a conditional volatility-point opportunity/risk ratio, not a probability, expected return, or payoff forecast. Valid ratios: {sum(r['Buy Ratio (%)'] is not None for r in rows)} / {len(rows)}.")

st.subheader("Filters")
f1,f2,f3,f4,f5=st.columns(5)
with f1: min_ratio=st.number_input("Minimum Buy Ratio (%)",value=0.0)
with f2: min_ivfv=st.number_input("Minimum IV/FV",min_value=0.0,value=0.0,step=0.1)
with f3: max_iv_pct=st.number_input("Maximum IV percentile",min_value=0.0,max_value=100.0,value=100.0)
with f4: rv_filter=st.selectbox("RV trend",["All","Accelerating","Decelerating"])
with f5: quality_filter=st.selectbox("Minimum data quality",["Any","20 comparable IV sessions","50 comparable IV sessions"])
display_rows=[]
for row in rows:
    if query and query not in row["Symbol"]: continue
    if min_ratio>0 and (row["Buy Ratio (%)"] is None or row["Buy Ratio (%)"]<min_ratio): continue
    if min_ivfv>0 and (row["IV/FV"] is None or row["IV/FV"]<min_ivfv): continue
    if max_iv_pct<100 and (row["IV percentile"] is None or row["IV percentile"]>max_iv_pct): continue
    minimum_history={"Any":0,"20 comparable IV sessions":20,"50 comparable IV sessions":50}[quality_filter]
    if result_by_symbol[row["Symbol"]]["History count"] < minimum_history: continue
    if rv_filter=="Accelerating" and (row["RV trend"] is None or row["RV trend"]<=0): continue
    if rv_filter=="Decelerating" and (row["RV trend"] is None or row["RV trend"]>=0): continue
    display_rows.append(row)
st.dataframe(display_rows,width="stretch",hide_index=True)
st.caption("Fair IV uses a 10-session EWMA (0.8 decay by default) of prior-session 20-day realized volatility from imported CM closes. Historical option IV remains separate for downside references and IV percentiles. Large returns are retained and flagged for review.")
st.caption("Sector filters and sector-relative IV percentile are unavailable because the current database has no authoritative sector classification; no sector values are inferred.")
st.caption("Earnings-event IV analysis is unavailable: no reliable earnings calendar or event history is stored in the current database.")

with st.expander("Stock detail and ratio breakdown"):
    detail_symbol=st.selectbox("Stock",symbols,key="detail_symbol")
    detail=result_by_symbol.get(detail_symbol,{})
    per_stock_holding=st.number_input("This stock holding period (trading days)",min_value=1,max_value=30,value=int(holding_days),key="stock_holding")
    if detail:
        st.write({"Current IV":detail.get("IV"),"Fair IV":detail.get("Fair IV"),"IV/FV":detail.get("IV/FV"),
            "HV5":detail.get("HV5"),"HV20":detail.get("HV20"),"Historical minimum IV":detail.get("Minimum IV"),
            "Historical median IV":detail.get("Median IV"),"Selected downside IV":detail.get("Downside reference"),
            "DTE":detail.get("DTE"),"Comparable IV observations":detail.get("History count"),"Data quality":detail.get("Warnings")})
        # Recompute for the per-stock holding-period override.
        if detail.get("IV") and detail.get("Fair IV") and detail.get("Theta") is not None and detail.get("Vega"):
            detail_ref=detail.get("Downside reference")
            if int(per_stock_holding)>=max(1,int(detail.get("DTE",0)*252/365)):
                detail_ratio={"buy_ratio":None,"warning":"Holding period reaches or exceeds remaining expiry time."}
            else:
                detail_ratio=volatility_buy_ratio(detail["IV"],detail["Fair IV"],detail["Theta"],detail["Vega"],detail_ref,int(per_stock_holding),float(config.get("buy_ratio_denominator_floor_iv_points",1e-6)))
            st.write({"Per-stock Buy Ratio (%)":detail_ratio.get("buy_ratio"),"Volatility-point breakdown":detail_ratio})
        if detail.get("IV") and detail.get("Fair IV") and detail.get("Vega"):
            sensitivity=[]
            for horizon in (1,3,5,10):
                line={"Holding days":horizon}
                for n in (20,30,50):
                    refs=downside_references(detail.get("History",[]),n)
                    ref={"Historical minimum":refs["minimum"],"Historical median":refs["median"],"Fair IV":detail.get("Fair IV")}[downside_reference]
                    expiry_horizon=max(1,int(detail.get("DTE",0)*252/365))
                    calc=(volatility_buy_ratio(detail["IV"],detail["Fair IV"],detail.get("Theta"),detail["Vega"],ref,horizon,float(config.get("buy_ratio_denominator_floor_iv_points",1e-6)))
                          if refs["observations"]>=20 and horizon<expiry_horizon else {"buy_ratio":None})
                    line[f"{n}-session downside"] = calc.get("buy_ratio")
                sensitivity.append(line)
            st.markdown("**Buy Ratio sensitivity (%)**")
            st.dataframe(sensitivity,width="stretch",hide_index=True)
        hist=detail.get("History",[])
        if hist:
            st.line_chart({"ATM IV":hist})
        st.caption("Sensitivity: holding period and downside lookback can be changed in the sidebar; the selected stock's ratio is shown above using its per-stock holding override.")
conn.close()
