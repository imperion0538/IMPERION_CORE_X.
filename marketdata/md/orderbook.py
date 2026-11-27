"""Emir defteri katmanı: Binance perp/spot, Bybit, OKX ve Coinbase defterlerinden dakikalık derinlik görüntüsü.

Her görüntüde: fiyatın ±%0,5/1/2/5 aralığındaki alış ve satış likiditesi (USD), dengesizlik oranı,
borsalar birleşik fiyat kovalarında en büyük emir duvarları ve ısı haritası için kova bazlı derinlik.
"""
import asyncio, json, logging, math, time
from datetime import datetime, timedelta, timezone
from . import config, db, http

log = logging.getLogger("orderbook")
UTC = timezone.utc
BANDS = (0.5, 1, 2, 5)
HEAT_RANGE_PCT = 3.0  # ısı haritası için fiyatın ±%3'ü saklanır
WALL_MIN_DIST_PCT = 0.2  # en iyi alış/satışa yapışık kademeler "duvar" sayılmaz

def nice_step(price, frac=0.0008):
    """Fiyatın yaklaşık %0,08'i kadar 'yuvarlak' kova genişliği (BTC ~$50, ETH ~$1, SOL ~$0,05)."""
    raw = price * frac
    p = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * p:
            return m * p
    return 10 * p

async def _binance(c, spot=False):
    url = "https://api.binance.com/api/v3/depth" if spot else "https://fapi.binance.com/fapi/v1/depth"
    d = await http.get(url, {"symbol": c + "USDT", "limit": 1000})
    return [(float(p), float(q)) for p, q in d["bids"]], [(float(p), float(q)) for p, q in d["asks"]]

async def _bybit(c):
    d = (await http.get("https://api.bybit.com/v5/market/orderbook", {"category": "linear", "symbol": c + "USDT", "limit": 500}))["result"]
    return [(float(p), float(q)) for p, q in d["b"]], [(float(p), float(q)) for p, q in d["a"]]

async def _okx(c):
    ct = config.OKX_CTVAL.get(c, 1)
    d = (await http.get("https://www.okx.com/api/v5/market/books-full", {"instId": f"{c}-USDT-SWAP", "sz": 5000}))["data"][0]
    return [(float(x[0]), float(x[1]) * ct) for x in d["bids"]], [(float(x[0]), float(x[1]) * ct) for x in d["asks"]]

async def _coinbase(c):
    d = await http.get(f"https://api.exchange.coinbase.com/products/{c}-USD/book", {"level": 2})
    return [(float(x[0]), float(x[1])) for x in d["bids"][:3000]], [(float(x[0]), float(x[1])) for x in d["asks"][:3000]]

SOURCES = {"binance_perp": _binance, "binance_spot": lambda c: _binance(c, True), "bybit": _bybit, "okx": _okx, "coinbase": _coinbase}

def summarize(books, mid, step):
    """Saf fonksiyon. books: {kaynak: (bids, asks)} -> bantlar, dengesizlik, duvarlar, ısı kovaları."""
    bands = {f"{b}": {"bid_usd": 0.0, "ask_usd": 0.0} for b in BANDS}
    per_venue = {}
    heat_b, heat_a = {}, {}
    for venue, (bids, asks) in books.items():
        vb = va = 0.0
        for side, levels in (("bid", bids), ("ask", asks)):
            for p, q in levels:
                usd = p * q
                dist = abs(p / mid - 1) * 100
                for b in BANDS:
                    if dist <= b + 1e-9: bands[f"{b}"][f"{side}_usd"] += usd
                if dist <= 2:
                    if side == "bid": vb += usd
                    else: va += usd
                if dist <= HEAT_RANGE_PCT:
                    k = round(round(p / step) * step, 8)
                    tgt = heat_b if side == "bid" else heat_a
                    tgt[k] = tgt.get(k, 0.0) + usd
        per_venue[venue] = {"bid_usd_2pct": vb, "ask_usd_2pct": va, "levels": len(bids) + len(asks),
                            "depth_pct": max([abs(p / mid - 1) * 100 for p, _ in (bids[-1:] + asks[-1:])] or [0])}
    for b in bands.values():
        t = b["bid_usd"] + b["ask_usd"]; b["imbalance"] = (b["bid_usd"] - b["ask_usd"]) / t if t else 0.0
    walls = {"bid": sorted(({"price": k, "usd": v, "dist_pct": (k / mid - 1) * 100} for k, v in heat_b.items() if k < mid * (1 - WALL_MIN_DIST_PCT / 100)), key=lambda x: -x["usd"])[:5],
             "ask": sorted(({"price": k, "usd": v, "dist_pct": (k / mid - 1) * 100} for k, v in heat_a.items() if k > mid * (1 + WALL_MIN_DIST_PCT / 100)), key=lambda x: -x["usd"])[:5]}
    return {"bands": bands, "venues": per_venue, "walls": walls,
            "heat_bids": {f"{k}": round(v) for k, v in heat_b.items()}, "heat_asks": {f"{k}": round(v) for k, v in heat_a.items()}}

async def snapshot(c):
    res = await asyncio.gather(*[f(c) for f in SOURCES.values()], return_exceptions=True)
    books = {}
    for name, r in zip(SOURCES, res):
        if isinstance(r, Exception):
            log.debug("%s %s defter hatası: %s", name, c, r)
        elif r[0] and r[1] and max(p for p,q in r[0]) < min(p for p,q in r[1]) and all(
                math.isfinite(p) and math.isfinite(q) and p>0 and q>=0 for levels in r for p,q in levels):
            books[name] = r
    if not books:
        return None
    ref = books.get("binance_perp") or next(iter(books.values()))
    mid = (ref[0][0][0] + ref[1][0][0]) / 2
    step = nice_step(mid)
    s = summarize(books, mid, step)
    s.update({"coin": c, "mid": mid, "step": step, "sources": list(books),
              "missing_sources": [n for n in SOURCES if n not in books], "collection_mode": "REST sampled",
              "note": "Finite depth; reported bands may be only partly visible. Sources are not synchronized."})
    return s

LATEST: dict = {}

async def poll():
    while True:
        t0 = time.time()
        for c in config.COINS:
            try:
                s = await snapshot(c)
            except Exception as e:
                log.warning("defter %s hatası: %s", c, e); s = None
            if not s: continue
            LATEST[c] = {**s, "ts": datetime.now(UTC).isoformat()}
            try:
                async with db.pool.acquire() as cn:
                    await cn.execute("INSERT INTO depth_snap (ts, coin, mid, step, data) VALUES (date_trunc('minute', now()), $1, $2, $3, $4::jsonb) "
                                     "ON CONFLICT (coin, ts) DO UPDATE SET mid=EXCLUDED.mid, step=EXCLUDED.step, data=EXCLUDED.data",
                                     c, s["mid"], s["step"], json.dumps(s))
            except Exception as e:
                log.warning("defter yazma hatası: %s", e)
        from . import state
        newest = max((datetime.fromisoformat(d["ts"]).timestamp() for d in LATEST.values()), default=0)
        state.stream_health["orderbook"] = {"last_msg": newest, "connected": bool(time.time()-newest<120), "reconnects": 0, "msgs": len(LATEST)}
        await asyncio.sleep(max(5, 60 - (time.time() - t0)))

async def history(coin, hours=6):
    async with db.pool.acquire() as cn:
        rows = await cn.fetch("SELECT ts, mid, step, data->'heat_bids' b, data->'heat_asks' a FROM depth_snap WHERE coin=$1 AND ts > now() - $2::interval ORDER BY ts",
                              coin, timedelta(hours=hours))
    return [{"ts": r["ts"], "mid": r["mid"], "step": r["step"], "bids": json.loads(r["b"]), "asks": json.loads(r["a"])} for r in rows]

async def latest(coin):
    if coin in LATEST:
        result = dict(LATEST[coin])
    else:
        async with db.pool.acquire() as cn:
            r = await cn.fetchrow("SELECT data, ts FROM depth_snap WHERE coin=$1 ORDER BY ts DESC LIMIT 1", coin)
        if not r:
            return None
        result = {**json.loads(r["data"]), "ts": r["ts"].isoformat()}
    result["age_seconds"] = max(0, time.time()-datetime.fromisoformat(result["ts"]).timestamp())
    result["stale"] = result["age_seconds"]>120
    return result
