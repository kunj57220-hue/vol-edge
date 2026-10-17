"""Historical, DTE-comparable ATM IV derived only from imported NSE observations."""
from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from datetime import date

from .volatility import implied_volatility


def iv_for_expiry(rows: list[tuple], spot: float, trade_date: str,
                  expiry: str, rate: float, dividend: float) -> float | None:
    """Average valid ATM call/put IV for one imported session and expiry."""
    days = (date.fromisoformat(expiry) - date.fromisoformat(trade_date)).days
    if days < 1 or spot <= 0:
        return None
    strikes: dict[float, dict[str, tuple[float, int]]] = defaultdict(dict)
    for strike, opt, close, contracts in rows:
        if opt in ("CE", "C", "PE", "P") and close is not None and close > 0 and contracts:
            strikes[float(strike)][opt] = (float(close), int(contracts))
    sides = [(v.get("CE", v.get("C")), v.get("PE", v.get("P")))
             for k, v in strikes.items() if ("CE" in v or "C" in v) and ("PE" in v or "P" in v)]
    if not sides:
        return None
    strike = min((k for k, v in strikes.items()
                  if ("CE" in v or "C" in v) and ("PE" in v or "P" in v)), key=lambda k: abs(k - spot))
    pair = strikes[strike]
    call, put = pair.get("CE", pair.get("C")), pair.get("PE", pair.get("P"))
    civ = implied_volatility(call[0], spot, strike, days / 365, rate, "C", dividend)
    piv = implied_volatility(put[0], spot, strike, days / 365, rate, "P", dividend)
    return (civ + piv) / 2 if civ is not None and piv is not None else None


def historical_atm_iv_series(conn: sqlite3.Connection, symbol: str, asof: str,
                             target_dte: int, lookback: int = 50,
                             rate: float = 0.06, dividend: float = 0.0) -> tuple[list[float], int, int, int]:
    """Return IVs normalized to the current DTE using total-variance interpolation.

    For each date, ATM annualized variance is interpolated in total variance across
    listed expiries. If there is no expiry bracket, a nearest expiry is accepted only
    within max(5 calendar days, 25% of target DTE). Materially different tenors are
    omitted and counted for dashboard warnings.
    Returns (chronological IVs, included dates, omitted dates, material DTE mismatches).
    """
    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM fo_eod WHERE symbol=? AND trade_date<? "
        "ORDER BY trade_date DESC LIMIT ?", (symbol, asof, lookback))]
    if not dates or target_dte < 1:
        return [], 0, len(dates), 0
    oldest = min(dates)
    cash = {d: p for d, p in conn.execute(
        "SELECT trade_date,close FROM cash_eod WHERE symbol=? AND series IN ('EQ','') "
        "AND trade_date BETWEEN ? AND ? AND close>0", (symbol, oldest, asof))}
    rows = conn.execute(
        "SELECT trade_date,expiry,strike,option_type,close,contracts FROM fo_eod "
        "WHERE symbol=? AND trade_date BETWEEN ? AND ? AND trade_date<? "
        "AND instrument IN ('OPTSTK','STO') AND expiry>trade_date AND close>0 "
        "ORDER BY trade_date,expiry,strike", (symbol, oldest, asof, asof)).fetchall()
    by_day_expiry: dict[str, dict[str, list[tuple]]] = defaultdict(lambda: defaultdict(list))
    for d, e, k, typ, close, contracts in rows:
        by_day_expiry[d][e].append((k, typ, close, contracts))
    result: list[tuple[str, float]] = []
    material_mismatch = 0
    tolerance = max(5.0, target_dte * 0.25)
    for d in dates:
        spot = cash.get(d)
        if not spot:
            continue
        curve = []
        for expiry, chain in by_day_expiry.get(d, {}).items():
            dte = (date.fromisoformat(expiry) - date.fromisoformat(d)).days
            iv = iv_for_expiry(chain, spot, d, expiry, rate, dividend)
            if iv is not None and dte > 0:
                curve.append((dte, iv))
        curve.sort()
        below = [x for x in curve if x[0] <= target_dte]
        above = [x for x in curve if x[0] >= target_dte]
        if below and above:
            lo, hi = below[-1], above[0]
            if lo[0] == hi[0]:
                iv = lo[1]
            else:
                weight = (target_dte - lo[0]) / (hi[0] - lo[0])
                variance = lo[1] ** 2 * lo[0] + weight * (hi[1] ** 2 * hi[0] - lo[1] ** 2 * lo[0])
                iv = math.sqrt(max(variance, 0) / target_dte)
            result.append((d, iv))
        elif curve:
            nearest = min(curve, key=lambda x: abs(x[0] - target_dte))
            if abs(nearest[0] - target_dte) > tolerance:
                material_mismatch += 1
            else:
                result.append((d, nearest[1]))
    result.sort()
    return [iv for _, iv in result], len(result), max(0, len(dates) - len(result)), material_mismatch
