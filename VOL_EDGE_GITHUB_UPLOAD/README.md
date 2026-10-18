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

Proposed architecture: Render Free native Python web service for the existing dashboard, Neon Free PostgreSQL for compact persistent market history, and `.github/workflows/update-nse.yml` for weekday NSE EOD imports. The service is configured in `render.yaml` for Singapore with auto-deploy from `main` and a secret prompt for `VOL_EDGE_DATABASE_URL`. This requires no Git installation, VPS, Docker server, custom domain, or paid plan.

Keep `data/vol_edge.sqlite3` as the full local archive. The 66.7 MB compact seed is built from that archive separately, preserving the data needed by fair volatility, HV5/HV20, IV percentile, comparable-DTE historical IV, and Buy Ratio. The hosted updater retains 120 paired sessions. It needs no laptop after the initial seed and database credentials are added as a Render secret and a GitHub Actions secret.

Neon's current Free plan provides 1 GB PostgreSQL per project, 100 compute-hours and 5 GB public network transfer per month, with compute suspended after 5 minutes idle. The app and updater monitor database storage at 60%, 75%, and 90%; `VOL_EDGE_DB_SIZE_LIMIT_BYTES` can override the 1 GB monitor threshold. Free Neon has limited restore history and no production uptime commitment. Render Free services sleep after 15 minutes without incoming requests and can take about a minute to wake. Their filesystem is ephemeral, so the app must use Neon for persistence. Render may also suspend Free services for unusually high external traffic, including database requests; monitor usage and avoid repeated unnecessary reloads. GitHub Actions runs can be delayed, and schedules in public repositories are disabled after 60 days without repository activity.

Deployment is not active. Account authorization is still required to link the GitHub repository to Render and create/configure the Neon database.

The cached scenario-P&L functions under `vol_edge/trade.py` are retained for historical tests, but the dashboard no longer uses them for ranking.
