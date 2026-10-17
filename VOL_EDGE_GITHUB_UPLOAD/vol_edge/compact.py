"""Compact ATM option representation preserving dashboard IV calculations."""
from __future__ import annotations

from collections import defaultdict


def compact_option_rows(rows: list[tuple], spot_by_symbol: dict[str, float]) -> list[tuple]:
    """Keep paired ATM rows per symbol/expiry, plus nearest liquid pair if different.

    Current IV needs the nearest paired strike even when its volume field is zero;
    historical ATM IV chooses the nearest paired strike with both legs traded.
    Retaining both selections exactly preserves those two existing selection rules.
    """
    grouped: dict[tuple[str, str, str, str], dict[float, dict[str, tuple]]] = defaultdict(lambda: defaultdict(dict))
    for row in rows:
        day, instrument, symbol, expiry, strike, option_type = row[:6]
        if instrument not in ("OPTSTK", "STO") or option_type not in ("CE", "PE", "C", "P"):
            continue
        grouped[(day, instrument, symbol, expiry)][float(strike)][option_type] = row
    kept = {}
    for (day, instrument, symbol, expiry), strikes in grouped.items():
        spot = spot_by_symbol.get(symbol)
        if spot is None or spot <= 0:
            continue
        def pair_complete(legs, require_volume=False):
            call, put = legs.get("CE", legs.get("C")), legs.get("PE", legs.get("P"))
            if call is None or put is None or call[9] is None or put[9] is None:
                return False
            if call[9] <= 0 or put[9] <= 0:
                return False
            return not require_volume or (call[11] and put[11])
        current = [k for k, legs in strikes.items() if pair_complete(legs)]
        liquid = [k for k, legs in strikes.items() if pair_complete(legs, True)]
        selections = []
        if current:
            selections.append(min(current, key=lambda k: abs(k - spot)))
        if liquid:
            selections.append(min(liquid, key=lambda k: abs(k - spot)))
        for strike in selections:
            for row in strikes[strike].values():
                # The dashboard and historical IV code use only option rows, not futures.
                kept[(day, instrument, symbol, expiry, strike, row[5])] = row
    return list(kept.values())


def compact_cash_rows(rows):
    """Keep the EQ series used for spot, HV and Fair IV; retain all dates/symbols."""
    return [row for row in rows if row[2] in ("EQ", "") and row[6] is not None and row[6] > 0]
