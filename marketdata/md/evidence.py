"""Zamansal sızıntı olmadan, çakışmayan dört saatlik sonuçların araştırması.

Only closed hourly candles. Each forecast evaluation uses outcomes observable before
that evaluation time. Historical frequencies are not a calibrated live win probability.
"""
import math
import statistics


def regime(closes):
    fast, slow = statistics.fmean(closes[-20:]), statistics.fmean(closes[-50:])
    trend = 1 if fast/slow-1 > .002 else -1 if fast/slow-1 < -.002 else 0
    move = closes[-1]/closes[-5]-1
    momentum = 1 if move > .005 else -1 if move < -.005 else 0
    return trend, momentum


def wilson(successes, n):
    if not n:
        return None
    z = 1.96
    p = successes/n
    center = (p+z*z/(2*n))/(1+z*z/n)
    width = z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
    return [max(0, center-width), min(1, center+width)]


def quantile(xs, q):
    xs = sorted(xs)
    i = (len(xs)-1)*q
    lo = int(i)
    return xs[lo]+(xs[min(lo+1, len(xs)-1)]-xs[lo])*(i-lo)


def evaluate(rows, horizon=4, minimum=30):
    if len(rows) < 80 or any(b["ts"]-a["ts"] != 3600 for a, b in zip(rows, rows[1:])):
        return {"status": "insufficient_history", "research_only": True, "samples": 0}
    closes = [r["close"] for r in rows]
    target = regime(closes)
    history, forecasts, baselines = [], [], []
    for i in range(50, len(rows)-horizon, horizon):
        key = regime(closes[:i+1])
        # Previous outcomes end at/before this observation; no overlapping labels.
        eligible = [h for h in history if h["end"] <= i]
        matched = [h for h in eligible if h["regime"] == key]
        outcome = closes[i+horizon]/closes[i]-1
        if len(matched) >= minimum:
            p = sum(h["return"] > 0 for h in matched)/len(matched)
            baseline = sum(h["return"] > 0 for h in eligible)/len(eligible)
            y = float(outcome > 0)
            forecasts.append((p-y)**2)
            baselines.append((baseline-y)**2)
        history.append({"end": i+horizon, "regime": key, "return": outcome})
    matches = [h["return"]*100 for h in history if h["regime"] == target]
    n = len(matches)
    out = {"status": "descriptive_only" if n >= minimum else "insufficient_samples",
           "research_only": True, "horizon_hours": horizon, "samples": n,
           "history_bars": len(rows), "nonoverlapping": True,
           "method": "20/50 SMA trend and 4h momentum buckets; expanding-window evaluation, purged horizons",
           "walk_forward": {"samples": len(forecasts),
                            "brier": statistics.fmean(forecasts) if forecasts else None,
                            "baseline_brier": statistics.fmean(baselines) if baselines else None},
           "live_success_probability": None,
           "limitations": "Candle-only analogy; no flow/options history in this model. No fees, funding or execution outcome. No profitability claim."}
    if n >= minimum:
        out.update({"historical_positive_fraction": sum(x > 0 for x in matches)/n,
                    "positive_fraction_ci95": wilson(sum(x > 0 for x in matches), n),
                    "return_pct": {"p10": quantile(matches, .1), "median": quantile(matches, .5), "p90": quantile(matches, .9)}})
    return out
