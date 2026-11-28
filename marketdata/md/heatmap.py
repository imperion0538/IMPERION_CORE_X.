"""Çoklu borsa tahmini likidasyon ısı haritası ve gerçekleşen likidasyon haritası.

Model: Binance + Bybit + OKX open interest geçmişinde her artış, o mumun kapanış fiyatından yeni pozisyon
olarak kabul edilir (yarısı long, yarısı short) ve kaldıraç dağılımına göre likidasyon fiyatlarına yayılır.
OI düşüşü tüm açık seviyeleri orantılı azaltır; fiyatın geçtiği seviyeler likide olmuş sayılıp silinir.
"""
import asyncio, time
from datetime import datetime, timedelta, timezone
from . import db, http

UTC = timezone.utc
LEVERAGE = [(5, .10), (10, .25), (20, .25), (25, .15), (50, .15), (100, .10)]
WINDOWS = {"1h": ("5m", 3600, 0.05), "4h": ("5m", 4 * 3600, 0.08), "24h": ("5m", 86400, 0.12),
           "7d": ("1h", 7 * 86400, 0.20), "30d": ("1h", 30 * 86400, 0.25)}
BN = "https://fapi.binance.com"

async def _klines(coin, interval, start_ms):
    out, cur = [], start_ms
    while True:
        k = await http.get(f"{BN}/fapi/v1/klines", {"symbol": coin + "USDT", "interval": interval, "startTime": cur, "limit": 1500})
        out += k
        if len(k) < 1500: break
        cur = k[-1][0] + 1
    return {int(x[0]): (float(x[2]), float(x[3]), float(x[4])) for x in out}

async def _oi_binance(coin, interval, start_ms):
    out, end = {}, int(time.time() * 1000)
    while end > start_ms:
        r = await http.get(f"{BN}/futures/data/openInterestHist", {"symbol": coin + "USDT", "period": interval, "limit": 500, "endTime": end})
        if not r: break
        for x in r: out[int(x["timestamp"])] = float(x["sumOpenInterestValue"])
        first = min(int(x["timestamp"]) for x in r)
        if first >= end or len(r) < 500: break
        end = first - 1
    return {t: v for t, v in out.items() if t >= start_ms}

async def _oi_bybit(coin, interval, start_ms, prices):
    iv = {"5m": "5min", "1h": "1h"}[interval]
    out, cursor = {}, None
    for _ in range(20):
        p = {"category": "linear", "symbol": coin + "USDT", "intervalTime": iv, "limit": 200, "startTime": start_ms, "endTime": int(time.time() * 1000)}
        if cursor: p["cursor"] = cursor
        r = (await http.get("https://api.bybit.com/v5/market/open-interest", p))["result"]
        for x in r["list"]:
            t = int(x["timestamp"]); px = prices.get(t)
            if px: out[t] = float(x["openInterest"]) * px[2]
        cursor = r.get("nextPageCursor")
        if not cursor or not r["list"]: break
    return out

async def _oi_okx(coin, interval, start_ms):
    r = await http.get("https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-volume", {"ccy": coin, "period": {"5m": "5m", "1h": "1H"}[interval]})
    return {int(x[0]): float(x[1]) for x in r["data"] if int(x[0]) >= start_ms}

async def _safe(coro):
    try: return await coro
    except Exception: return {}

async def model(coin="BTC", window="24h"):
    async def build():
        interval, secs, rng = WINDOWS[window]
        start = int((time.time() - secs) * 1000) - (300000 if interval == "5m" else 3600000)
        prices = await _klines(coin, interval, start)
        bn, ok = await asyncio.gather(_safe(_oi_binance(coin, interval, start)), _safe(_oi_okx(coin, interval, start)))
        by = await _safe(_oi_bybit(coin, interval, start, prices))
        series = {"binance": bn, "bybit": by, "okx": ok}
        ref, bins, used = simulate(prices, series, rng)
        return {"coin": coin, "window": window, "price": ref, "interval": interval, "venues_used": {v: k for v, k in used.items() if k},
                "bins": bins, "clusters": clusters(bins, ref), "generated_utc": datetime.now(UTC).strftime("%Y-%m-%d %H:%M"),
                "kind": "assumed_leverage_model", "not_exchange_liquidations": True}
    return await http.cached(f"heat:{coin}:{window}", 120, build)

def simulate(prices, series, rng, n=50, leverage=LEVERAGE):
    """Saf model. prices: {ts: (high, low, close)}, series: {borsa: {ts: oi_usd}}.
    Döndürür: (referans fiyat, kovalar, borsa başına kullanılan nokta sayısı)."""
    ts_list = sorted(prices)
    if not ts_list:
        raise ValueError("fiyat verisi yok")
    pos, prev = [], {}
    used = {v: 0 for v in series}
    for t in ts_list:
        h, l, c = prices[t]
        pos = [p for p in pos if not ((p[1] == "L" and l <= p[0]) or (p[1] == "S" and h >= p[0]))]
        delta = 0.0
        for v, s in series.items():
            if t in s:
                if v in prev:
                    delta += s[t] - prev[v]; used[v] += 1
                prev[v] = s[t]
        if delta > 0:
            for lev, w in leverage:
                pos.append([c * (1 - 1 / lev), "L", delta * w / 2]); pos.append([c * (1 + 1 / lev), "S", delta * w / 2])
        elif delta < 0:
            tot = sum(p[2] for p in pos)
            if tot > 0:
                f = max(0.0, 1 + delta / tot)
                for p in pos: p[2] *= f
    ref = prices[ts_list[-1]][2]
    step = rng / n
    bins = []
    for i in range(-n, n):
        lo, hi = ref * (1 + i * step), ref * (1 + (i + 1) * step)
        bins.append({"lo": lo, "hi": hi, "mid": (lo + hi) / 2,
                     "long_usd": sum(p[2] for p in pos if p[1] == "L" and lo <= p[0] < hi),
                     "short_usd": sum(p[2] for p in pos if p[1] == "S" and lo <= p[0] < hi)})
    return ref, bins, used

def clusters(bins, ref, k=3):
    out = []
    for side, col in (("long", "long_usd"), ("short", "short_usd")):
        top = sorted([b for b in bins if b[col] > 0], key=lambda b: -b[col])[:k]
        for b in top:
            out.append({"side": side, "price": b["mid"], "usd": b[col], "dist_pct": (b["mid"] / ref - 1) * 100})
    return out

async def realized(coin="BTC", window="24h", n=40):
    secs = {"1h": 3600, "4h": 14400, "24h": 86400, "7d": 604800, "30d": 2592000}.get(window, 86400)
    async with db.pool.acquire() as c:
        rows = await c.fetch("SELECT venue, side, price, usd FROM liquidations WHERE coin=$1 AND ts > now() - $2::interval", coin, timedelta(seconds=secs))
    if not rows:
        return {"coin": coin, "window": window, "kind": "observed_liquidation_prints", "not_a_model": True,
                "total_long": 0, "total_short": 0, "by_venue": {}, "bins": []}
    lo, hi = min(r["price"] for r in rows), max(r["price"] for r in rows)
    step = (hi - lo) / n or 1
    bins = [{"lo": lo + i * step, "hi": lo + (i + 1) * step, "long_usd": 0.0, "short_usd": 0.0} for i in range(n)]
    byv = {}
    for r in rows:
        i = min(n - 1, int((r["price"] - lo) / step))
        bins[i]["long_usd" if r["side"] == "long" else "short_usd"] += r["usd"]
        d = byv.setdefault(r["venue"], {"long": 0.0, "short": 0.0}); d[r["side"]] += r["usd"]
    return {"coin": coin, "window": window, "kind": "observed_liquidation_prints", "not_a_model": True,
            "total_long": sum(b["long_usd"] for b in bins), "total_short": sum(b["short_usd"] for b in bins),
            "by_venue": byv, "count": len(rows), "bins": bins}
