"""Piyasa genişliği ve açıkça yapılandırılmış makro olaylar; eksik beslemeler uydurulmaz."""
import json
import math
import os
import statistics
from datetime import datetime, timezone
from . import http


async def breadth():
    async def build():
        data = await http.get("https://fapi.binance.com/fapi/v1/ticker/24hr")
        universe = sorted((r for r in data if r["symbol"].endswith("USDT")
                           and float(r["quoteVolume"]) > 10_000_000), key=lambda r: -float(r["quoteVolume"]))[:50]
        moves = [float(r["priceChangePercent"]) for r in universe]
        return {"source": "Binance USDT perpetual · top 50 by 24h turnover", "sample_count": len(moves),
                "generated_utc": datetime.now(timezone.utc).isoformat(),
                "positive_fraction": sum(x > 0 for x in moves)/len(moves) if moves else None,
                "median_change_pct": statistics.median(moves) if moves else None,
                "leaders": [{"symbol": r["symbol"], "change_pct": float(r["priceChangePercent"])}
                            for r in sorted(universe, key=lambda r: -float(r["priceChangePercent"]))[:5]],
                "laggards": [{"symbol": r["symbol"], "change_pct": float(r["priceChangePercent"])}
                             for r in sorted(universe, key=lambda r: float(r["priceChangePercent"]))[:5]]}
    return await http.cached("market-breadth", 120, build)


def external_feeds():
    events = []
    status = "not_configured"
    raw = os.environ.get("MACRO_EVENTS_JSON", "")
    if raw:
        try:
            for row in json.loads(raw):
                ts = datetime.fromisoformat(row["at_utc"].replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    raise ValueError("timezone required")
                seconds = (ts-datetime.now(timezone.utc)).total_seconds()
                if -3600 <= seconds <= 7*86400:
                    events.append({"name": str(row["name"])[:160], "at_utc": ts.isoformat(),
                                   "minutes_until": round(seconds/60), "source": "operator configuration"})
            status = "configured_manual"
        except (ValueError, KeyError, TypeError):
            status = "invalid_configuration"
    result = {"onchain_entity_flows": {"status": "not_configured", "source": None,
                                      "reason": "No verified exchange-address labels for calculating net deposits/withdrawals."}}
    if raw:
        result["manual_events"] = {"status": status, "events": events, "source": "operator configuration"}
    return result



def risk_budget(equity, risk_pct, entry, stop, fee_bps=5, slippage_bps=5):
    values = (equity, risk_pct, entry, stop, fee_bps, slippage_bps)
    if (not all(math.isfinite(x) for x in values) or min(equity, entry, stop) <= 0
            or not 0 < risk_pct <= 100 or not 0 <= fee_bps <= 100 or not 0 <= slippage_bps <= 1000 or entry == stop):
        raise ValueError("Invalid risk inputs")
    cost = (entry+stop)*(fee_bps+slippage_bps)/10000
    budget = equity*risk_pct/100
    quantity = budget/(abs(entry-stop)+cost)
    return {"risk_budget_usd": budget, "quantity_base": quantity, "notional_usd": quantity*entry,
            "stop_distance_pct": abs(entry-stop)/entry*100, "modeled_cost_usd": quantity*cost,
            "assumed_fee_bps_per_side": fee_bps, "assumed_slippage_bps_per_side": slippage_bps,
            "note": "Stop execution and loss cap are not guaranteed. Funding, gaps and liquidation not modeled."}
