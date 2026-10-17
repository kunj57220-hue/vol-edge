# VOL EDGE

Streamlit NSE F&O long-volatility screener using imported CM/FO end-of-day bhavcopies and SQLite history. It does not provide intraday data.

## Local Windows run

```powershell
cd "C:\path\to\project-vol-edge-nse-long-volatility"
.\.venv\Scripts\Activate.ps1
python -m streamlit run app.py
```

The dashboard can import official NSE CM/FO CSV or ZIP files, or resume the latest 90 paired sessions with **Update Data**. It ranks eligible stocks by volatility-point Buy Ratio. The formula is documented in `vol_edge/buy_ratio.py`; the input assumptions are in `config.json`. Missing or non-comparable observations stay blank and carry data-quality warnings.

The fair-IV default is a normalized EWMA over 10 prior sessions with 0.8 decay; simple average is selectable. Historical IVs use total-variance interpolation to current DTE when expiries bracket it; without a bracket, a nearby expiry is included only within five days or 25% of target DTE. More distant tenors are omitted with a warning. The downside default is the minimum over 50 prior sessions. Theta/vega is in IV percentage points per day. The ratio is conditional and is not a probability or return forecast.

## Free cloud deployment (prepared, not deployed)

The free deployment uses Streamlit Community Cloud for the dashboard, Supabase Free PostgreSQL for persistent compact data, and `.github/workflows/update-nse.yml` for an independent weekday NSE updater. No paid VPS, Docker server, or paid domain is needed.

The full local SQLite database remains the historical archive. Build the smaller seed with `python scripts/build_compact_snapshot.py`, validate it with `python scripts/validate_compact_snapshot.py`, then seed Supabase once with `python scripts/seed_supabase.py`. The seed retains CM EQ closes and, for each stock/date/expiry, the paired nearest-ATM quotes needed for current IV and comparable-DTE historical IV. It preserves the existing fair-volatility and Buy Ratio calculations.

Set the `VOL_EDGE_DATABASE_URL` GitHub Actions secret and the `database_url` Streamlit secret to the Supabase PostgreSQL connection string. Never place either value in source code or commit a secrets file. The Streamlit app is read-only in hosted mode; scheduled Actions update the database without depending on a dashboard visit or the local laptop. The updater retries transient errors, skips weekends and listed market holidays (and NSE archive 404s), and retains at least 120 paired sessions. The dashboard and updater warn as allocated database storage passes 60%, 75%, and 90% of the Supabase Free 500 MB limit.

Deployment is not active. Supabase Free can pause projects after inactivity; the scheduled weekday database activity is intended to keep the project active, but the Supabase dashboard should still be checked if scheduled runs fail.

The cached scenario-P&L functions under `vol_edge/trade.py` are retained for historical tests, but the dashboard no longer uses them for ranking.
