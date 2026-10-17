"""Dependency-free realized volatility, Black-Scholes IV, and Greeks."""
from __future__ import annotations

import math
from statistics import NormalDist

N = NormalDist()


def realized_volatility(closes: list[float], window: int, trading_days: int = 252) -> float | None:
    """Annualized close-to-close log-return sample standard deviation; needs window+1 closes."""
    if window < 2 or len(closes) < window + 1 or any(p is None or p <= 0 for p in closes[-window-1:]):
        return None
    returns = [math.log(b / a) for a, b in zip(closes[-window-1:-1], closes[-window:])]
    mean = sum(returns) / window
    variance = sum((x - mean) ** 2 for x in returns) / (window - 1)
    return math.sqrt(variance * trading_days)


def realized_volatility_warnings(observations: list[tuple[str, float]], hv5: float | None,
                                 hv20: float | None, return_threshold: float = 0.10,
                                 relative_hv_gap: float = 0.25) -> list[str]:
    """Flag large observed close returns and material HV5/HV20 differences; never alter data."""
    warnings = []
    large = []
    ordered = sorted((d, float(p)) for d, p in observations if p is not None and p > 0)
    for (prev_date, prev), (day, close) in zip(ordered, ordered[1:]):
        move = math.log(close / prev)
        if abs(move) >= return_threshold:
            large.append(f"{day} {move:+.1%} close return (from {prev_date})")
    if large:
        warnings.append("Large close return(s) require review; retained without winsorizing: " + ", ".join(large))
    if hv5 is not None and hv20 is not None and hv20 > 0:
        gap = abs(hv5 - hv20) / hv20
        if gap >= relative_hv_gap:
            warnings.append(f"HV5 differs from HV20 by {gap:.0%}; short- and medium-window volatility diverge materially.")
    return warnings


def _cdf(x: float) -> float:
    return N.cdf(x)


def bs_price(spot: float, strike: float, years: float, rate: float, vol: float,
             option_type: str, dividend: float = 0.0) -> float:
    if spot <= 0 or strike <= 0 or years < 0 or vol < 0:
        raise ValueError("spot, strike must be positive; years and volatility nonnegative")
    typ = option_type.upper()
    if typ not in ("C", "CE", "CALL", "P", "PE", "PUT"):
        raise ValueError("option_type must identify a call or put")
    call = typ in ("C", "CE", "CALL")
    if years == 0 or vol == 0:
        fwd_spot = spot * math.exp(-dividend * years)
        pv_strike = strike * math.exp(-rate * years)
        return max((fwd_spot - pv_strike) if call else (pv_strike - fwd_spot), 0.0)
    root_t = math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate - dividend + .5 * vol * vol) * years) / (vol * root_t)
    d2 = d1 - vol * root_t
    if call:
        return spot * math.exp(-dividend * years) * _cdf(d1) - strike * math.exp(-rate * years) * _cdf(d2)
    return strike * math.exp(-rate * years) * _cdf(-d2) - spot * math.exp(-dividend * years) * _cdf(-d1)


def implied_volatility(price: float, spot: float, strike: float, years: float,
                       rate: float = .06, option_type: str = "C", dividend: float = 0.0,
                       tolerance: float = 1e-8, max_iterations: int = 200) -> float | None:
    """Bisection inversion. Returns None for prices outside no-arbitrage bounds."""
    if price < 0 or spot <= 0 or strike <= 0 or years <= 0:
        return None
    call = option_type.upper() in ("C", "CE", "CALL")
    lower = max(spot * math.exp(-dividend*years) - strike*math.exp(-rate*years), 0) if call else max(strike*math.exp(-rate*years)-spot*math.exp(-dividend*years),0)
    upper = spot * math.exp(-dividend*years) if call else strike * math.exp(-rate*years)
    if price < lower - tolerance or price >= upper or price <= 0:
        return None
    lo, hi = 1e-6, 5.0
    while hi < 100 and bs_price(spot,strike,years,rate,hi,option_type,dividend) < price:
        hi *= 2
    if bs_price(spot,strike,years,rate,hi,option_type,dividend) < price:
        return None
    for _ in range(max_iterations):
        mid = (lo + hi) / 2
        model = bs_price(spot,strike,years,rate,mid,option_type,dividend)
        if abs(model-price) <= tolerance:
            return mid
        if model < price: lo = mid
        else: hi = mid
    return (lo+hi)/2


def greeks(spot: float, strike: float, years: float, rate: float, vol: float,
           option_type: str, dividend: float = 0.0) -> dict[str, float]:
    """Per underlying unit; theta is per calendar day, vega per 1 volatility point."""
    call = option_type.upper() in ("C", "CE", "CALL")
    if years <= 0 or vol <= 0:
        return {"delta": 1.0 if call and spot>strike else (-1.0 if not call and spot<strike else 0.0),
                "gamma":0.0,"theta_per_day":0.0,"vega_per_vol_point":0.0}
    root_t=math.sqrt(years); d1=(math.log(spot/strike)+(rate-dividend+.5*vol*vol)*years)/(vol*root_t); d2=d1-vol*root_t
    pdf=math.exp(-.5*d1*d1)/math.sqrt(2*math.pi)
    delta=math.exp(-dividend*years)*(_cdf(d1) if call else _cdf(d1)-1)
    gamma=math.exp(-dividend*years)*pdf/(spot*vol*root_t)
    vega=spot*math.exp(-dividend*years)*pdf*root_t/100
    theta=(-(spot*math.exp(-dividend*years)*pdf*vol)/(2*root_t)
           -rate*strike*math.exp(-rate*years)*(_cdf(d2) if call else _cdf(-d2))
           +dividend*spot*math.exp(-dividend*years)*(_cdf(d1) if call else -_cdf(-d1)))/365
    return {"delta":delta,"gamma":gamma,"theta_per_day":theta,"vega_per_vol_point":vega}


def percentile_rank(history: list[float], value: float) -> float | None:
    """Empirical percentile with midpoint ties; None until there are at least 20 observations."""
    clean=[x for x in history if x is not None and math.isfinite(x)]
    if len(clean)<20: return None
    below=sum(x<value for x in clean); tied=sum(x==value for x in clean)
    return 100*(below+.5*tied)/len(clean)
