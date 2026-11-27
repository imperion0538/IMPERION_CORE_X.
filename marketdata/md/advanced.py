"""Advanced research computed from observed market data, not trade advice."""
import asyncio
import math
import statistics
from datetime import datetime, timedelta, timezone
from . import analysis, config, db, http, state

UTC = timezone.utc

def number(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (ValueError, TypeError, OverflowError):
        return None

def anomaly(history, latest):
    """Compare a closed bucket to earlier closed buckets; never train on itself."""
    latest = number(latest)
    history = [x for v in history if (x := number(v)) is not None]
    if len(history) < 12 or latest is None:
        return {"status": "insufficient_data", "z": None, "samples": len(history)}
    sd = statistics.stdev(history)
    if not sd:
        return {"status": "zero_variance", "z": None, "samples": len(history)}
    z = (latest - statistics.mean(history)) / sd
    return {"status": "observed", "z": round(z, 3), "samples": len(history),
            "anomalous": abs(z) >= 3}

def cascade(price, clusters, funding, cvd, liquidation, coverage, venues):
    """Heuristic stress index, not a probability. Fail closed on missing inputs."""
    p, f, d, l = map(number, (price, funding, cvd, liquidation))
    if (p is None or p <= 0 or f is None or d is None or l is None or l < 0
            or coverage < .8 or not venues):
        return {"status": "insufficient_data", "score": None, "level": None}
    proximity = 0.0
    for c in clusters:
        k, usd = number(c.get("price")), number(c.get("usd"))
        if k is None or usd is None or k <= 0 or usd < 0:
            continue
        dist = abs(k / p - 1)
        if dist <= .03:
            proximity += min(usd / 10_000_000, 5) * math.exp(-dist * 40) * 20
    components = {"proximity": min(55, proximity),
                  "funding": min(15, abs(f) / 100 * 15),
                  "flow_pressure": min(15, abs(d) / 20_000_000 * 10),
                  "observed_liquidations": min(15, l / 5_000_000 * 10)}
    score = min(100, sum(components.values()))
    return {"status": "estimated", "score": round(score, 2),
            "level": "HIGH" if score >= 60 else "MEDIUM" if score >= 30 else "LOW",
            "components": {k: round(v, 2) for k, v in components.items()},
            "method": "heuristic_v1; absolute USD thresholds, not calibrated probabilities"}

def aggregate_options(rows, start_ms, end_ms, partial=False):
    """Inverse BTC/ETH option premiums; unsupported units are rejected."""
    buy = sell = call = put = 0.0
    seen = set()
    large = []
    rejected = 0
    for t in rows:
        ident = t.get("trade_id")
        ts = number(t.get("timestamp"))
        instrument = str(t.get("instrument_name", ""))
        pieces = instrument.split("-")
        if (not ident or ts is None or not start_ms <= ts < end_ms
                or len(pieces) != 4 or pieces[0] not in ("BTC", "ETH")
                or pieces[-1] not in ("C", "P") or t.get("direction") not in ("buy", "sell")):
            rejected += 1
            continue
        if ident in seen:
            continue
        amount, price, index = map(number, (t.get("amount"), t.get("price"), t.get("index_price")))
        if amount is None or price is None or index is None or min(amount, price, index) <= 0:
            rejected += 1
            continue
        premium = amount * price * index
        if not math.isfinite(premium):
            rejected += 1
            continue
        seen.add(ident)
        if t["direction"] == "buy":
            buy += premium
        else:
            sell += premium
        if pieces[-1] == "C":
            call += premium
        else:
            put += premium
        if premium >= 100_000:
            large.append({"instrument": instrument, "direction": t["direction"], "premium_usd": premium,
                          "timestamp": int(ts), "trade_id": ident})
    return {"status": "partial" if partial or rejected else "observed",
            "trade_count": len(seen), "rejected": rejected,
            "buy_premium_usd": round(buy, 2), "sell_premium_usd": round(sell, 2),
            "net_premium_usd": round(buy-sell, 2), "call_premium_usd": round(call, 2),
            "put_premium_usd": round(put, 2), "put_call_premium_ratio": round(put/call, 4) if call else None,
            "large_trades": sorted(large, key=lambda t: -t["premium_usd"])[:20],
            "interpretation": "aggressor premium flow; does not reveal opening/closing or dealer inventory"}

async def option_flow(coin):
    if coin not in ("BTC", "ETH"):
        return {"status": "unsupported", "coin": coin}
    async def build():
        end = int(datetime.now(UTC).timestamp() * 1000)
        start = end - 900_000
        try:
            response = await http.get("https://www.deribit.com/api/v2/public/get_last_trades_by_currency_and_time",
                {"currency": coin, "kind": "option", "start_timestamp": start,
                 "end_timestamp": end-1, "count": 1000, "sorting": "desc"})
            result = response["result"]
            trades = result["trades"]
            if not isinstance(trades, list):
                raise ValueError("invalid trades")
            output = aggregate_options(trades, start, end, bool(result.get("has_more")) or len(trades) >= 1000)
            return dict(output, coin=coin, source="Deribit", window="15m",
                        start_ms=start, end_ms=end)
        except Exception:
            return {"status": "unavailable", "coin": coin, "source": "Deribit", "window": "15m",
                    "start_ms": start, "end_ms": end}
    return await http.cached(f"option-flow:{coin}", 30, build)

async def observed_history(coin):
    now = datetime.now(UTC)
    end = now.replace(minute=now.minute//5*5, second=0, microsecond=0)
    async with db.pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT date_bin('5 minutes',ts,'2000-01-01') AS bucket, venue, market,
                   count(*) AS minutes, sum(buy_usd)-sum(sell_usd) AS delta
            FROM trades_1m WHERE coin=$1 AND ts >= $2 AND ts < $3
            GROUP BY 1,2,3 ORDER BY 1
            """, coin, end-timedelta(hours=6), end)
        liq = await conn.fetchval("SELECT sum(usd) FROM liquidations WHERE coin=$1 AND ts >= $2 AND ts < $3",
                                 coin, now-timedelta(minutes=5), now)
    by_source = {}
    for row in rows:
        if row["minutes"] == 5:
            by_source.setdefault((row["venue"], row["market"]), {})[row["bucket"]] = row["delta"]
    last = end-timedelta(minutes=5)
    stats = []
    for (venue, market), values in sorted(by_source.items()):
        z = anomaly([v for t,v in values.items() if t < last], values.get(last))
        stats.append(dict(z, venue=venue, market=market, bucket_utc=last.isoformat()))
    return stats, liq

async def research(coin):
    async def build():
        a, hist, opt = await asyncio.gather(analysis.analysis(coin), observed_history(coin), option_flow(coin),
                                          return_exceptions=True)
        result = {"coin": coin, "generated_utc": datetime.now(UTC).isoformat(),
                  "options_flow": opt if isinstance(opt,dict) else {"status": "unavailable"},
                  "anomalies": hist[0] if isinstance(hist,tuple) else [],
                  "cascade": {"status":"unavailable","score":None,"level":None}}
        if isinstance(a,dict) and isinstance(hist,tuple):
            flow = a["flows"]["4h"]
            cov = flow["coverage"]
            expected = cov["expected_minutes"]
            perp = [s for s in cov["sources"] if s["market"] == "perp"]
            coverage = max((s["observed_minutes"]/expected for s in perp), default=0) if expected else 0
            cvd = flow["cvd"].get("perp",{}).get("delta")
            # No events does not prove zero liquidations when source health is unknown.
            healthy = any(name.endswith("_liq") and time_ok(h) for name,h in state.stream_health.items())
            liq = hist[1] if hist[1] is not None else 0 if healthy else None
            hm = a["heatmap_24h"]
            result["cascade"] = cascade(a["price"],hm["clusters"],a["derivatives"]["funding_ann_pct"],
                                        cvd,liq,coverage,hm["venues_used"])
            result["coverage"] = {"max_perp_source_fraction": coverage, "sources": perp,
                                  "liquidation_feed_recent": healthy}
        return result
    return await http.cached(f"advanced:{coin}", 30, build)

def time_ok(h):
    return datetime.now(UTC).timestamp() - h.get("last_msg",0) <= 120
