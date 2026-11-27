"""Deribit zinciri için vade bazlı ödeme ve duyarlılık ölçümleri."""
from collections import defaultdict
from math import erf, exp, log, pi, sqrt

YEAR = 365 * 86400


def greeks(forward, strike, iv, seconds, rate, side):
    """Black-76: forward deltası/gamması, IV puanı başına vega, gün başına theta."""
    t = seconds / YEAR
    root = sqrt(t)
    d1 = (log(forward / strike) + iv * iv * t / 2) / (iv * root)
    d2 = d1 - iv * root
    discount = exp(-rate * t)
    sign = 1 if side == "C" else -1

    def cdf(x):
        return (1 + erf(x / sqrt(2))) / 2

    density = exp(-d1 * d1 / 2) / sqrt(2 * pi)
    premium = discount * sign * (forward * cdf(sign * d1) - strike * cdf(sign * d2))
    return {
        "delta": discount * sign * cdf(sign * d1),
        "gamma": discount * density / (forward * iv * root),
        "vega": discount * forward * density * root / 100,
        "theta": (rate * premium - discount * forward * density * iv / (2 * root)) / 365,
    }


def expiry_metrics(rows, spot, seconds):
    sides = defaultdict(lambda: {"call_oi": 0.0, "put_oi": 0.0, "signed_gex_usd_1pct": 0.0})
    weighted = dict.fromkeys(("delta", "gamma", "vega", "theta"), 0.0)
    gross = covered = 0.0
    for row in rows:
        strike, side, oi = row["strike"], row["side"], row["oi"]
        block = sides[strike]
        block["call_oi" if side == "C" else "put_oi"] += oi
        forward, iv = row["forward"], row["iv"]
        if oi <= 0 or forward <= 0 or iv <= 0:
            continue
        values = greeks(forward, strike, iv, seconds, row["rate"], side)
        covered += oi
        for name, value in values.items():
            weighted[name] += value * oi
        exposure = values["gamma"] * oi * spot ** 2 * .01
        gross += exposure
        block["signed_gex_usd_1pct"] += exposure if side == "C" else -exposure

    strikes = sorted(sides)
    call_oi = sum(b["call_oi"] for b in sides.values())
    put_oi = sum(b["put_oi"] for b in sides.values())
    total = call_oi + put_oi

    def payout(settlement):
        return sum(b["call_oi"] * max(0, settlement - k) + b["put_oi"] * max(0, k - settlement)
                   for k, b in sides.items())

    curve = [{"strike": strike, "intrinsic_payout_usd": payout(strike), **sides[strike]} for strike in strikes]
    pain = min(curve, key=lambda x: x["intrinsic_payout_usd"])
    return {
        "oi_usd": total * spot, "call_oi": call_oi, "put_oi": put_oi,
        "put_call": put_oi / max(1e-9, call_oi),
        "max_pain": pain["strike"], "max_pain_payout_usd": pain["intrinsic_payout_usd"],
        "max_pain_distance_pct": (pain["strike"] / spot - 1) * 100,
        "payout_at_index_usd": payout(spot),
        "gex_gross_usd_1pct": gross if covered else None,
        "gex_signed_proxy_usd_1pct": sum(b["signed_gex_usd_1pct"] for b in sides.values()) if covered else None,
        "greeks_oi_coverage_pct": covered / total * 100 if total else 0,
        "greeks_oi_weighted": {name: value / covered for name, value in weighted.items()} if covered else None,
        "top_strike_gex": sorted(curve, key=lambda x: abs(x["signed_gex_usd_1pct"]), reverse=True)[:5] if covered else [],
        "strike_profile": curve,
    }
