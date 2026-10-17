"""Transparent V1 long volatility screening score."""
from __future__ import annotations
import math

DEFAULT_WEIGHTS = {"forward_rv_vs_iv":.25,"hv_acceleration":.20,"iv_percentile":.15,
                   "iv_vs_fair":.15,"iv_momentum_skew":.10,"price_range":.10,"regime":.05}


def opportunity_score(factors: dict[str,float|None], weights: dict[str,float] | None=None) -> dict:
    """Factors are 0..100, with higher always better. Missing factors are excluded and weights renormalized."""
    w = DEFAULT_WEIGHTS if weights is None else weights
    if set(w) != set(DEFAULT_WEIGHTS) or any(v < 0 for v in w.values()) or sum(w.values()) <= 0:
        raise ValueError("Weights must cover all factors, be nonnegative, and have positive total")
    present={k:max(0,min(100,float(factors[k]))) for k in w if factors.get(k) is not None}
    denom=sum(w[k] for k in present)
    if not present or denom == 0: return {"score":None,"contributions":{}}
    contrib={k:present[k]*w[k]/denom for k in present}
    return {"score":sum(contrib.values()),"contributions":contrib}


def long_vol_factor_scores(iv: float, expected_rv: float, hv5: float|None,
                           hv20: float|None, iv_percentile: float|None=None,
                           fair_iv: float|None=None) -> dict[str,float|None]:
    """Map available observations to monotone long-vol factor scores; IV/RV edge saturates at +/-20%."""
    relative=(expected_rv-iv)/iv if iv>0 else 0
    rv=max(0,min(100,50+relative*250))
    accel=None if not hv5 or not hv20 or hv20<=0 else max(0,min(100,50+((hv5/hv20)-1)*100))
    return {"forward_rv_vs_iv":rv,"hv_acceleration":accel,
        "iv_percentile":None if iv_percentile is None else max(0,min(100,100-iv_percentile)),
        "iv_vs_fair":None if fair_iv is None or iv<=0 else max(0,min(100,50+(fair_iv-iv)/iv*250)),
        "iv_momentum_skew":None,"price_range":None,"regime":None}


def raw_long_vol_factors(iv: float, expected_rv: float, hv5: float|None,
                         hv20: float|None, iv_percentile: float|None=None,
                         fair_iv: float|None=None) -> dict[str,float|None]:
    """Unsaturated raw factors; positive direction always means more attractive long vol."""
    return {
        "forward_rv_vs_iv":(expected_rv-iv)/iv if iv>0 else None,
        "hv_acceleration":(hv5/hv20)-1 if hv5 is not None and hv20 is not None and hv20>0 else None,
        "iv_percentile":iv_percentile,
        "iv_vs_fair":(fair_iv-iv)/iv if fair_iv is not None and iv>0 else None,
        "iv_momentum_skew":None,"price_range":None,"regime":None,
    }


def cross_sectional_opportunity_scores(raw_by_symbol: dict[str,dict[str,float|None]],
                                       weights: dict[str,float]|None=None) -> dict[str,dict]:
    """Percentile-normalize each available raw factor; ties receive their midrank percentile."""
    w=DEFAULT_WEIGHTS if weights is None else weights
    normalized={symbol:{} for symbol in raw_by_symbol}
    for factor in w:
        values=[(s,float(v[factor])) for s,v in raw_by_symbol.items()
                if v.get(factor) is not None and math.isfinite(float(v[factor]))]
        if not values: continue
        ordered=sorted(value for _,value in values); n=len(ordered)
        for symbol,value in values:
            below=sum(x<value for x in ordered); tied=sum(x==value for x in ordered)
            percentile=100*(below+.5*tied)/n
            normalized[symbol][factor]=100-percentile if factor=="iv_percentile" else percentile
    return {symbol:opportunity_score(normalized[symbol],w) | {"normalized_factors":normalized[symbol],
             "raw_factors":{k:raw_by_symbol[symbol].get(k) for k in w}} for symbol in raw_by_symbol}
