"""NSE bhavcopy import, validation, and explicit file download helpers."""
from __future__ import annotations

import csv
import hashlib
import http.client
import io
import json
import logging
import os
import random
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .db import connect, is_postgres
from .compact import compact_cash_rows, compact_option_rows

_download_logger=logging.getLogger("vol_edge.downloads")
if not _download_logger.handlers:
    _download_logger.setLevel(logging.WARNING)
    Path("data").mkdir(parents=True,exist_ok=True)
    _download_logger.addHandler(logging.FileHandler("data/download_failures.log",encoding="utf-8"))

# Official NSE CM/F&O trading holidays for 2026. Update this calendar when NSE
# publishes its annual circular; archive 404 handling covers ad-hoc closures.
NSE_HOLIDAYS = {
    date(2026,1,15),date(2026,1,26),date(2026,3,3),date(2026,3,26),date(2026,3,31),
    date(2026,4,3),date(2026,4,14),date(2026,5,1),date(2026,5,28),date(2026,6,26),
    date(2026,9,14),date(2026,10,2),date(2026,10,20),date(2026,11,8),
    date(2026,11,10),date(2026,11,24),date(2026,12,25),
}


def _norm(row: dict[str, str]) -> dict[str, str]:
    return {str(k).strip().upper(): (v or "").strip() for k, v in row.items() if k}


def _float(row: dict[str, str], *names: str, default: float | None = None) -> float | None:
    for name in names:
        value = row.get(name)
        if value not in (None, "", "-", "NA"):
            try:
                return float(value.replace(",", ""))
            except ValueError:
                return default
    return default


def _int(row: dict[str, str], *names: str) -> int | None:
    v = _float(row, *names)
    return int(v) if v is not None else None


def _csv_rows(payload: bytes) -> tuple[list[dict[str, str]], str]:
    raw = payload
    if payload[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            candidates = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not candidates:
                raise ValueError("ZIP contains no CSV file")
            raw = zf.read(candidates[0])
    text = raw.decode("utf-8-sig")
    rows = [_norm(r) for r in csv.DictReader(io.StringIO(text))]
    if not rows:
        raise ValueError("Bhavcopy is empty or has no header/data rows")
    return rows, hashlib.sha256(payload).hexdigest()


def import_bhavcopy(payload: bytes, segment: str, trade_date: date | str,
                    source: str = "manual-upload", db_path: str | Path = "data/vol_edge.sqlite3") -> int:
    """Import recognized NSE CM/FO CSV/ZIP; reject unknown layouts without partial writes."""
    segment = segment.upper()
    if segment not in {"CM", "FO"}:
        raise ValueError("segment must be CM or FO")
    day = trade_date.isoformat() if isinstance(trade_date, date) else str(trade_date)
    rows, digest = _csv_rows(payload)
    def file_day(row: dict[str,str]) -> str:
        value=row.get("TRADDT", "")
        if value:
            try: return date.fromisoformat(value[:10]).isoformat()
            except ValueError: pass
        return day
    udiff = "TCKRSYMB" in rows[0] or "FININSTRMTP" in rows[0]
    if segment == "CM":
        if not (({"SYMBOL", "CLOSE"}.issubset(rows[0])) or ("TCKRSYMB" in rows[0] and "CLSPRIC" in rows[0])):
            raise ValueError("Unrecognized CM schema; expected legacy SYMBOL/CLOSE or UDiFF TckrSymb/ClsPric")
        staged = []
        for r in rows:
            symbol = r.get("TCKRSYMB", r.get("SYMBOL"))
            if not symbol:
                continue
            staged.append((file_day(r), symbol, r.get("SCTYSRS", r.get("SERIES")),
                _float(r,"OPNPRIC","OPEN"), _float(r,"HG HPRIC","HG HPRIC","HGHPRIC","HIGH"),
                _float(r,"LWPRIC","LOW"), _float(r,"CLSPRIC","CLOSE"), _float(r,"LASTPRIC","LAST"),
                _float(r,"PRVSC LSGPRIC","PRVSCLSGPRIC","PREVCLOSE","PREV. CLOSE"),
                _int(r,"TTLTRADGVOL","TOTTRDQTY","TOTTRD QTY","VOLUME"),
                _float(r,"TTLTRFVAL","TOTTRDVAL","TOTTRD VAL","TURNOVER"),
                _int(r,"TTLNB OFTXSEXCTD","TTLNB OFTXSEXCTD","TTLNB OFTXSEXCTD","TOTALTRADES"), r.get("ISIN")))
        if is_postgres(db_path):
            staged = compact_cash_rows(staged)
        cols = "trade_date,symbol,series,open,high,low,close,last,prev_close,volume,turnover,trades,isin"
        updates = "open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,last=excluded.last,prev_close=excluded.prev_close,volume=excluded.volume,turnover=excluded.turnover,trades=excluded.trades,isin=excluded.isin"
        sql = f"INSERT INTO cash_eod({cols}) VALUES ({','.join(['?']*13)}) ON CONFLICT(trade_date,symbol,series) DO UPDATE SET {updates}"
    else:
        required = {"INSTRUMENT", "SYMBOL", "EXPIRY_DT", "OPEN", "CLOSE"}
        udiff_required = {"FININSTRMTP", "TCKRSYMB", "XPRYDT", "OPNPRIC", "CLSPRIC"}
        if not (required.issubset(rows[0]) or udiff_required.issubset(rows[0])):
            raise ValueError("Unrecognized FO schema; expected INSTRUMENT, SYMBOL, EXPIRY_DT, OPEN, CLOSE")
        staged = []
        for r in rows:
            symbol = r.get("TCKRSYMB", r.get("SYMBOL"))
            if not symbol:
                continue
            exp = r.get("XPRYDT", r.get("EXPIRY_DT", ""))
            # NSE FO bhavcopy commonly uses DD-MMM-YYYY.
            for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y"):
                try:
                    from datetime import datetime
                    exp = datetime.strptime(exp, fmt).date().isoformat(); break
                except ValueError:
                    pass
            ftype=r.get("FININSTRMTP", "")
            inst = {"STF":"FUTSTK","STO":"OPTSTK","IDF":"FUTIDX","IDO":"OPTIDX"}.get(ftype, r.get("INSTRUMENT", ftype))
            staged.append((file_day(r), inst, symbol, exp,
                _float(r,"STRKPRIC","STRIKE_PR","STRIKE PRICE",default=0) or 0,
                r.get("OPTNTP", r.get("OPTION_TYP", r.get("OPTION TYPE", ""))),
                _float(r,"OPNPRIC","OPEN"), _float(r,"HGHPRIC","HIGH"),
                _float(r,"LWPRIC","LOW"), _float(r,"CLSPRIC","CLOSE"),
                _float(r,"STTL MPRIC","STTL MPRIC","STTLPRIC","SETTLE_PR","SETTLE PRICE"),
                _int(r,"TTLTRADGVOL","CONTRACTS"), _float(r,"TTLTRFVAL","VAL_INLAKH","VAL INLAKH","TURNOVER"),
                _int(r,"OPNINTRST","OPEN_INT","OPEN INT","OPEN_INTEREST"),
                _int(r,"CHNGINOPNINTRST","CHANGEINOPNINTRST","CHG_IN_OI","CHG IN OI","CHANGE_IN_OI"),
                _int(r,"NEWBRDLOTQTY","LOT_SIZE","MARKET LOT")))
        if is_postgres(db_path):
            spot_conn=connect(db_path)
            try:
                spots={s:p for s,p in spot_conn.execute(
                    "SELECT symbol,close FROM cash_eod WHERE trade_date=? AND series IN ('EQ','') AND close>0",(day,)).fetchall()}
            finally:
                spot_conn.close()
            staged = compact_option_rows(staged,spots)
        cols = "trade_date,instrument,symbol,expiry,strike,option_type,open,high,low,close,settle,contracts,turnover,open_interest,change_oi,lot_size"
        updates = "open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,settle=excluded.settle,contracts=excluded.contracts,turnover=excluded.turnover,open_interest=excluded.open_interest,change_oi=excluded.change_oi,lot_size=excluded.lot_size"
        sql = f"INSERT INTO fo_eod({cols}) VALUES ({','.join(['?']*16)}) ON CONFLICT(trade_date,instrument,symbol,expiry,strike,option_type) DO UPDATE SET {updates}"
    if not staged:
        raise ValueError("No rows had a usable symbol")
    conn = connect(db_path)
    try:
        with conn:
            conn.executemany(sql, staged)
            conn.execute("INSERT INTO bhavcopy_files(trade_date,segment,source,file_sha256,row_count) VALUES(?,?,?,?,?) ON CONFLICT(trade_date,segment) DO UPDATE SET source=excluded.source,file_sha256=excluded.file_sha256,imported_at=CURRENT_TIMESTAMP,row_count=excluded.row_count",
                         (day, segment, source, digest, len(staged)))
    finally:
        conn.close()
    return len(staged)


def nse_urls(trade_date: date) -> dict[str, str]:
    """Current public CM/FO archive URLs; availability depends on NSE archive policy."""
    d = trade_date
    day = d.strftime("%d%m%Y")
    if d >= date(2024, 7, 8):
        return {
            "CM": f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip",
            "FO": f"https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d:%Y%m%d}_F_0000.csv.zip",
        }
    return {
        "CM": f"https://archives.nseindia.com/content/historical/EQUITIES/{d:%Y}/{d:%b}/cm{day}bhav.csv.zip",
        "FO": f"https://archives.nseindia.com/content/historical/DERIVATIVES/{d:%Y}/{d:%b}/fo{day}bhav.csv.zip",
    }


def download_bhavcopy(trade_date: date, segment: str, timeout: int = 30) -> bytes:
    url = nse_urls(trade_date)[segment.upper()]
    request = Request(url, headers={"User-Agent": "VolEdge/1.0 (personal NSE EOD archive download)"})
    with urlopen(request, timeout=timeout) as response:
        payload = response.read()
    if payload[:2] != b"PK":
        raise ValueError(f"NSE did not return a ZIP archive for {trade_date} {segment}; no data imported")
    return payload


def import_nse_day(trade_date: date, db_path: str | Path = "data/vol_edge.sqlite3") -> dict[str, int]:
    result = {}
    for segment, url in nse_urls(trade_date).items():
        try:
            payload = download_bhavcopy(trade_date, segment)
            result[segment] = import_bhavcopy(payload, segment, trade_date, url, db_path)
        except Exception as exc:
            if "HTTP Error 404" in str(exc):
                result[segment] = 0  # holiday/non-trading day archive absence
                continue
            raise
    return result


def _existing_sessions(db_path: str | Path) -> dict[str, set[str]]:
    conn=connect(db_path)
    try:
        out={"CM":set(),"FO":set()}
        for day,segment in conn.execute("SELECT trade_date,segment FROM bhavcopy_files"):
            if segment in out: out[segment].add(day)
        return out
    finally: conn.close()


def _log_request(day: date, segment: str, status: str, detail: str) -> None:
    _download_logger.warning("date=%s segment=%s status=%s detail=%s",day.isoformat(),segment,status,detail)


def _failed_dates(imported: dict[str,set[str]]) -> list[date]:
    path=Path("data/download_failures.log")
    if not path.exists(): return []
    days=set()
    import re
    for line in path.read_text(encoding="utf-8",errors="replace").splitlines():
        m=re.search(r"date=(\d{4}-\d{2}-\d{2}) segment=(CM|FO) status=FAILED",line)
        if m and m.group(1) not in imported[m.group(2)]:
            try: days.add(date.fromisoformat(m.group(1)))
            except ValueError: pass
    return sorted(days,reverse=True)


def download_status(db_path: str | Path = "data/vol_edge.sqlite3", sessions: int = 90) -> dict[str,int]:
    imported=_existing_sessions(db_path)
    completed=sum(len(v) for v in imported.values())
    failed_keys=set()
    path=Path("data/download_failures.log")
    if path.exists():
        import re
        for line in path.read_text(encoding="utf-8",errors="replace").splitlines():
            m=re.search(r"date=(\d{4}-\d{2}-\d{2}) segment=(CM|FO) status=FAILED",line)
            if m and m.group(1) not in imported[m.group(2)]: failed_keys.add((m.group(1),m.group(2)))
    failed=len(failed_keys)
    return {"completed":completed,"failed":failed,"pending":max(0,sessions*2-completed-failed)}


def prune_history(db_path: str | Path, keep_sessions: int = 120) -> dict[str, int]:
    """Retain recent paired sessions in compact cloud storage; local SQLite stays archival."""
    if keep_sessions < 90:
        raise ValueError("Cloud retention must preserve at least 90 sessions for IV history")
    conn=connect(db_path)
    try:
        days=[r[0] for r in conn.execute(
            "SELECT trade_date FROM bhavcopy_files GROUP BY trade_date HAVING COUNT(DISTINCT segment)=2 ORDER BY trade_date DESC LIMIT ?",
            (keep_sessions,))]
        if len(days)<keep_sessions:
            return {"deleted_cash_rows":0,"deleted_fo_rows":0,"deleted_files":0}
        cutoff=min(days)
        with conn:
            cash=conn.execute("DELETE FROM cash_eod WHERE trade_date<?",(cutoff,)).rowcount
            fo=conn.execute("DELETE FROM fo_eod WHERE trade_date<?",(cutoff,)).rowcount
            files=conn.execute("DELETE FROM bhavcopy_files WHERE trade_date<?",(cutoff,)).rowcount
        return {"deleted_cash_rows":cash,"deleted_fo_rows":fo,"deleted_files":files}
    finally:
        conn.close()


def update_history(db_path: str | Path = "data/vol_edge.sqlite3", sessions: int = 90,
                   end_date: date | None = None, progress=None, cache_dir: str | Path = "data/bhavcopies",
                   min_request_interval: float = 1.25, max_retries: int = 3,
                   retry_failed: bool = False) -> dict:
    """Fill the latest N complete CM+FO sessions; persist ZIPs and resume safely.

    NSE 404s are treated as non-session/unavailable and never bypassed. 403/429 and
    transient failures are reported, with bounded retries and a respectful delay.
    """
    if sessions < 1: raise ValueError("sessions must be positive")
    end_date=end_date or date.today()
    cache=Path(cache_dir); cache.mkdir(parents=True,exist_ok=True)
    imported=_existing_sessions(db_path)
    report={"target_sessions":sessions,"completed":0,"completed_files":0,"failed_files":0,"pending_files":sessions*2,
            "downloaded":[],"imported":[],"skipped":[],"missing":[],"failed":[],"blocked":False}
    last_request=0.0

    def emit(message, current=0):
        if progress: progress(message,current,sessions)

    # Walk backward until the requested count of complete paired sessions is found.
    # This naturally skips weekends and NSE holidays (archives return 404).
    day=end_date
    while day.weekday() >= 5: day -= timedelta(days=1)
    retry_days=_failed_dates(imported) if retry_failed else []
    scan_days=[]
    cursor=day
    for _ in range(sessions*3+30):
        if cursor.weekday()<5 and cursor not in NSE_HOLIDAYS: scan_days.append(cursor)
        cursor-=timedelta(days=1)
        while cursor.weekday()>=5: cursor-=timedelta(days=1)
    days_to_process=list(dict.fromkeys(retry_days+scan_days))
    for day in days_to_process:
        if report["completed"] >= sessions: break
        if day in NSE_HOLIDAYS:
            report["skipped"].append(f"{day.isoformat()} NSE trading holiday")
            continue
        day_s=day.isoformat()
        day_ok=True
        for segment in ("CM","FO"):
            if day_s in imported[segment]:
                report["skipped"].append(f"{day_s} {segment} already imported")
                report["completed_files"]+=1
                continue
            cached=cache/f"{segment}_{day_s}.zip"
            payload=cached.read_bytes() if cached.exists() else None
            if payload is None:
                url=nse_urls(day)[segment]
                for attempt in range(max_retries+1):
                    wait=max(0,min_request_interval-(time.monotonic()-last_request))
                    if wait: time.sleep(wait)
                    last_request=time.monotonic()
                    try:
                        payload=download_bhavcopy(day,segment)
                        cached.write_bytes(payload)
                        report["downloaded"].append(f"{day_s} {segment}")
                        break
                    except HTTPError as exc:
                        if exc.code == 404:
                            report["missing"].append(f"{day_s} {segment}: NSE archive unavailable (404)")
                            _log_request(day,segment,"MISSING",f"HTTP 404: {url}")
                            payload=None; break
                        if exc.code == 403:
                            report["blocked"]=True
                            msg=f"{day_s} {segment}: NSE access restricted (HTTP {exc.code}); reduce request rate or try later"
                            report["failed"].append(msg); emit(msg,report["completed"])
                            _log_request(day,segment,"FAILED",f"HTTP 403: {url}")
                            payload=None; break
                        if exc.code == 429:
                            if attempt >= max_retries:
                                report["blocked"]=True
                                msg=f"{day_s} {segment}: HTTP 429 after {attempt+1} attempts; NSE rate limit reached"
                                report["failed"].append(msg); _log_request(day,segment,"FAILED",f"{msg}; {url}")
                                payload=None; break
                            retry_after=exc.headers.get("Retry-After") if exc.headers else None
                            delay=min(float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt,120)
                            time.sleep(delay+random.uniform(0,.5))
                            continue
                        if attempt >= max_retries:
                            msg=f"{day_s} {segment}: HTTP {exc.code} after {attempt+1} attempts"
                            report["failed"].append(msg); _log_request(day,segment,"FAILED",f"{msg}; {url}")
                            payload=None; break
                        time.sleep(min(2**attempt,8)+random.uniform(0,.5))
                    except (URLError,TimeoutError,ConnectionError,OSError,http.client.HTTPException,ValueError,zipfile.BadZipFile) as exc:
                        if attempt >= max_retries:
                            msg=f"{day_s} {segment}: {type(exc).__name__}: {exc} after {attempt+1} attempts"
                            report["failed"].append(msg); _log_request(day,segment,"FAILED",f"{msg}; {url}")
                            payload=None; break
                        time.sleep(min(2**attempt,8)+random.uniform(0,.5))
            if payload is None:
                day_ok=False
                if report["blocked"]: break
                continue
            try:
                count=import_bhavcopy(payload,segment,day,url if not cached.exists() else str(cached),db_path)
                imported[segment].add(day_s)
                report["completed_files"]+=1
                report["imported"].append(f"{day_s} {segment}: {count:,} rows")
                _log_request(day,segment,"IMPORTED",f"{count} rows")
            except Exception as exc:
                day_ok=False
                msg=f"{day_s} {segment}: import failed: {type(exc).__name__}: {exc}"
                report["failed"].append(msg); _log_request(day,segment,"FAILED",msg)
                if cached.exists(): cached.unlink()
            emit(f"Processed {day_s} {segment}",report["completed"])
        if report["blocked"]: break
        if day_ok and day_s in imported["CM"] and day_s in imported["FO"]:
            report["completed"]+=1
    if report["completed"]<sessions and not report["blocked"]:
        report["failed"].append(f"Reached scan limit after finding {report['completed']} of {sessions} complete sessions")
    report["failed_files"]=sum(1 for m in report["failed"] if " CM:" in m or " FO:" in m)
    report["pending_files"]=max(0,sessions*2-report["completed_files"]-report["failed_files"])
    if is_postgres(db_path):
        report["retention_pruned"]=prune_history(db_path,120)
        storage_conn=connect(db_path)
        try:
            from .db import database_size_bytes
            report["database_size_bytes"]=database_size_bytes(storage_conn)
            size=report["database_size_bytes"] or 0
            size_limit=int(os.getenv("VOL_EDGE_DB_SIZE_LIMIT_BYTES", "1000000000"))
            if size>=size_limit*.9:
                report["storage_warning"]="CRITICAL: PostgreSQL database is above 90% of its configured Free-tier storage limit."
            elif size>=size_limit*.75:
                report["storage_warning"]="WARNING: PostgreSQL database is above 75% of its configured Free-tier storage limit."
            elif size>=size_limit*.6:
                report["storage_warning"]="NOTICE: PostgreSQL database is above 60% of its configured Free-tier storage limit."
        finally:
            storage_conn.close()
    emit("Update finished",report["completed"])
    return report
