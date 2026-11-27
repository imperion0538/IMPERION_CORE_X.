"""Kaynak başına veri yaşı, gecikme ve alım hataları. Sıfır, eksik veri değildir."""
import time
from collections import Counter, deque

TRADE_SOURCES = (
    ("binance", "spot"), ("binance", "perp"), ("bybit", "spot"),
    ("bybit", "perp"), ("okx", "spot"), ("okx", "perp"),
    ("coinbase", "spot"), ("hyperliquid", "perp"),
)
sources = {}
counters = Counter()


def observe(coin, venue, market, event_ts, received_ts):
    key = (coin, venue, market)
    s = sources.setdefault(key, {"last_event": 0, "last_received": 0,
                                 "events": 0, "latency": deque(maxlen=512)})
    s["last_event"] = max(s["last_event"], event_ts)
    s["last_received"] = received_ts
    s["events"] += 1
    s["latency"].append(max(0, received_ts - event_ts) * 1000)


def snapshot(coin, now=None):
    now = now if now is not None else time.time()
    rows = []
    for venue, market in TRADE_SOURCES:
        s = sources.get((coin, venue, market))
        latency = sorted(s["latency"]) if s else []
        age = max(0, now - s["last_event"]) if s else None
        rows.append({"venue": venue, "market": market,
                     "quote": "USD" if venue in ("coinbase", "hyperliquid") else "USDT",
                     "status": "missing" if age is None else "live" if age <= 30 else "stale",
                     "age_seconds": round(age, 2) if age is not None else None,
                     "receive_age_seconds": round(max(0, now - s["last_received"]), 2) if s else None,
                     "latency_p95_ms": round(latency[int((len(latency)-1)*.95)], 1) if latency else None,
                     "events_this_process": s["events"] if s else 0})
    live = sum(r["status"] == "live" for r in rows)
    return {"sources": rows, "live_sources": live, "expected_sources": len(rows),
            "status": "live" if live == len(rows) else "partial" if live else "unavailable",
            "ingestion": dict(counters),
            "notional_method": "USD/USDT notionals; USDT is not FX-adjusted. No complete-market coverage claim."}
