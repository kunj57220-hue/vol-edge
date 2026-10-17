"""Unhedged long ATM straddle, full repricing scenarios and payoff risk measures."""
from __future__ import annotations

from .volatility import bs_price, greeks


def option_round_trip_costs(entry_premium_per_unit: float, exit_straddle_per_unit: float,
                           lot_size: int, brokerage_per_order: float=20,
                           exchange_per_crore: float=3553, stt_sell_rate: float=.0015,
                           sebi_per_crore: float=10, stamp_buy_rate: float=.00003,
                           gst_rate: float=.18) -> dict[str,float]:
    """Approximate two-leg open/close costs; brokerage is four executions."""
    if min(entry_premium_per_unit,exit_straddle_per_unit,brokerage_per_order)<0 or lot_size<1:
        raise ValueError("premiums/costs must be nonnegative and lot size positive")
    entry=entry_premium_per_unit*lot_size
    exit_value=exit_straddle_per_unit*lot_size
    turnover=2*(entry+exit_value)
    exchange=turnover*exchange_per_crore/10_000_000
    sebi=turnover*sebi_per_crore/10_000_000
    stt=2*exit_value*stt_sell_rate
    stamp=2*entry*stamp_buy_rate
    brokerage=4*brokerage_per_order
    gst=(exchange+sebi+brokerage)*gst_rate
    return {"exchange":exchange,"sebi":sebi,"stt":stt,"stamp":stamp,
            "brokerage":brokerage,"gst":gst,"total":exchange+sebi+stt+stamp+brokerage+gst}


def straddle(spot: float, strike: float, days_to_expiry: int, iv: float, rate: float=.06,
             dividend: float=0, lot_size: int=1) -> dict:
    if days_to_expiry < 1 or lot_size < 1: raise ValueError("expiry days and lot size must be positive")
    t=days_to_expiry/365
    call=bs_price(spot,strike,t,rate,iv,"C",dividend); put=bs_price(spot,strike,t,rate,iv,"P",dividend)
    cg=greeks(spot,strike,t,rate,iv,"C",dividend); pg=greeks(spot,strike,t,rate,iv,"P",dividend)
    return {"call_premium":call,"put_premium":put,"premium_per_unit":call+put,
            "entry_cost_per_lot":(call+put)*lot_size,"lot_size":lot_size,
            "delta_per_unit":cg["delta"]+pg["delta"],"gamma_per_unit":cg["gamma"]+pg["gamma"],
            "theta_per_day_per_unit":cg["theta_per_day"]+pg["theta_per_day"],
            "vega_per_vol_point_per_unit":cg["vega_per_vol_point"]+pg["vega_per_vol_point"]}


def scenario_pnl(spot: float, strike: float, days_to_expiry: int, holding_days: int,
                 entry_iv: float, exit_iv: float, spot_move_fraction: float,
                 rate: float=.06, dividend: float=0, lot_size: int=1,
                 costs_per_unit: float=0, entry_premium_per_unit: float|None=None,
                 transaction_cost_per_lot: float|None=None) -> dict:
    if holding_days < 0 or holding_days >= days_to_expiry*252/365: raise ValueError("holding period must be before expiry")
    entry=straddle(spot,strike,days_to_expiry,entry_iv,rate,dividend,lot_size)
    premium=entry["premium_per_unit"] if entry_premium_per_unit is None else entry_premium_per_unit
    if premium < 0: raise ValueError("entry premium cannot be negative")
    exit_spot=spot*(1+spot_move_fraction); remaining=max(days_to_expiry/365-holding_days/252,0)
    exit_value=(bs_price(exit_spot,strike,remaining,rate,exit_iv,"C",dividend)+
                bs_price(exit_spot,strike,remaining,rate,exit_iv,"P",dividend))
    # Two option legs are opened and closed: four per-unit executions in a round trip.
    transaction_cost=4*costs_per_unit*lot_size if transaction_cost_per_lot is None else transaction_cost_per_lot
    entry_cost=premium*lot_size
    pnl=(exit_value-premium)*lot_size-transaction_cost
    return {"exit_spot":exit_spot,"exit_straddle_value_per_unit":exit_value,"net_pnl_per_lot":pnl,
            "entry_premium_per_unit":premium,"entry_cost_per_lot":entry_cost,
            "premium_at_risk_per_lot":entry_cost,"transaction_cost_per_lot":transaction_cost,
            "return_on_premium":pnl/entry_cost if entry_cost else None,
            "max_loss_per_lot":entry_cost+transaction_cost,
            "holding_trading_days":holding_days,"remaining_years":remaining}


def scenario_summary(scenarios: list[dict]) -> dict:
    """Each scenario has probability and net_pnl_per_lot. Probabilities must sum to one."""
    if not scenarios: raise ValueError("at least one scenario required")
    total=sum(s["probability"] for s in scenarios)
    if any(s["probability"]<0 for s in scenarios) or abs(total-1)>1e-8:
        raise ValueError("scenario probabilities must be nonnegative and sum to 1")
    ev=sum(s["probability"]*s["net_pnl_per_lot"] for s in scenarios)
    downside=sum(s["probability"]*max(0,-s["net_pnl_per_lot"]) for s in scenarios)
    return {"expected_pnl":ev,"expected_downside_loss":downside,
            "buy_ratio":ev/downside if downside>1e-12 else None,
            "downside_coverage":"modeled losses present" if downside>1e-12 else "insufficient downside coverage: no modeled losses",
            "probability_of_profit":sum(s["probability"] for s in scenarios if s["net_pnl_per_lot"]>0),
            "worst_scenario_pnl":min(s["net_pnl_per_lot"] for s in scenarios)}
