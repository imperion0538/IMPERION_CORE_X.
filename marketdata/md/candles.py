"""Kapalı mumlar; Binance USD-M kesilirse Bybit linear kaynağına açıkça işaretli geçiş."""
import math
import time
from . import http

INTERVALS = {"15m": (900, "15"), "1h": (3600, "60"), "4h": (14400, "240"), "1d": (86400, "D")}


def validate(rows, interval, now=None):
    now = time.time() if now is None else now
    period = INTERVALS[interval][0]
    out = {}
    for r in rows:
        ts, o, h, l, c, v = float(r[0])/1000, *map(float, r[1:6])
        if not all(math.isfinite(x) for x in (ts, o, h, l, c, v)):
            continue
        if ts+period > now or min(o, h, l, c) <= 0 or v < 0 or not l <= min(o, c) <= max(o, c) <= h:
            continue
        out[ts] = {"ts": ts, "close_ts": ts+period, "open": o, "high": h, "low": l, "close": c, "volume": v}
    return [out[k] for k in sorted(out)]


async def load(coin, interval, limit=300):
    async def fetch():
        errors = []
        for venue in ("binance", "bybit"):
            try:
                if venue == "binance":
                    rows = await http.get("https://fapi.binance.com/fapi/v1/klines",
                                          {"symbol": coin+"USDT", "interval": interval, "limit": limit})
                else:
                    d = await http.get("https://api.bybit.com/v5/market/kline", {
                        "category": "linear", "symbol": coin+"USDT", "interval": INTERVALS[interval][1], "limit": limit})
                    rows = d["result"]["list"]
                bars = validate(rows, interval)
                if len(bars) < 30:
                    raise ValueError("insufficient closed bars")
                if time.time()-bars[-1]["close_ts"] > INTERVALS[interval][0]+120:
                    raise ValueError("stale candles")
                gaps = sum(b["ts"]-a["ts"] != INTERVALS[interval][0] for a, b in zip(bars, bars[1:]))
                return {"source": venue+" USDT perpetual", "interval": interval, "rows": bars,
                        "gaps": gaps, "fallback": bool(errors), "source_errors": errors}
            except Exception as exc:
                errors.append({"source": venue, "error": type(exc).__name__})
        raise RuntimeError("Closed candles unavailable: "+str(errors))
    return await http.cached(f"candles:{coin}:{interval}:{limit}", 45, fetch)
