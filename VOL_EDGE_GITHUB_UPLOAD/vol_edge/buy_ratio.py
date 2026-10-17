"""Volatility-point Buy Ratio calculations for the VOL EDGE screener."""
from __future__ import annotations

import math
from statistics import median


def ewma(values: list[float | None], lookback: int = 10, decay: float = 0.8) -> float | None:
    """Normalized EWMA with the newest observation carrying weight 1, prior 0.8, etc."""
    if lookback < 1 or not 0 < decay <= 1:
        raise ValueError("lookback must be positive and decay in (0, 1]")
    valid = [float(v) for v in values if v is not None and math.isfinite(float(v)) and float(v) > 0]
    valid = valid[-lookback:]
    if not valid:
        return None
    weights = [decay ** (len(valid) - i - 1) for i in range(len(valid))]
    return sum(v * w for v, w in zip(valid, weights)) / sum(weights)


def downside_references(values: list[float | None], lookback: int = 50) -> dict[str, float | int | None]:
    """Historical IV min and median from valid, DTE-comparable annualized IV readings."""
    if lookback < 1:
        raise ValueError("lookback must be positive")
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v)) and float(v) > 0][-lookback:]
    return {"minimum": min(clean) if clean else None,
            "median": median(clean) if clean else None, "observations": len(clean)}


def volatility_buy_ratio(iv: float | None, fair_iv: float | None,
                         daily_theta: float | None, daily_vega: float | None,
                         downside_iv: float | None, holding_days: int = 5,
                         denominator_floor: float = 1e-6) -> dict:
    """Compute the conditional relative opportunity/risk ratio in IV percentage points.

    IV/FV/downside inputs are decimals (e.g. .25 = 25% annualized IV). Greeks are
    per underlying unit: theta per calendar day and vega per one IV percentage point.
    The ratio is not a probability or a mark-to-market return forecast.
    """
    required = (iv, fair_iv, daily_theta, daily_vega, downside_iv)
    if any(v is None or not math.isfinite(float(v)) for v in required):
        return {"buy_ratio": None, "warning": "Missing or invalid IV, fair IV, downside IV, theta, or vega."}
    if iv <= 0 or fair_iv <= 0 or downside_iv <= 0 or daily_vega <= 0 or holding_days < 1:
        return {"buy_ratio": None, "warning": "IV and vega must be positive and holding period at least one session."}
    # Greeks expose vega per one IV percentage point, so the quotient is already
    # in volatility points per day (do not apply another /100 conversion).
    theta_per_vega = abs(float(daily_theta)) / float(daily_vega)
    theta_points = theta_per_vega * holding_days
    expansion = max(float(fair_iv) - float(iv), 0.0) * 100.0
    compression = max(float(iv) - float(downside_iv), 0.0) * 100.0
    favorable = expansion + theta_points
    adverse = compression + theta_points
    ratio = None if adverse <= denominator_floor else 100.0 * favorable / adverse
    return {"buy_ratio": ratio, "iv_expansion_points": expansion,
            "theta_points": theta_points, "favorable_points": favorable,
            "iv_compression_points": compression, "adverse_points": adverse,
            "theta_points_per_day": theta_per_vega,
            "warning": "Adverse denominator is zero or immaterial." if ratio is None else None}
