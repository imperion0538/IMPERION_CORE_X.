"""Periyodik REST toplayıcılar: OI, funding, long/short, opsiyonlar; dakikalık kovaları veritabanına yazar."""
import asyncio, collections, json, logging, time
from datetime import datetime, timezone
from . import config, db, http, state

log = logging.getLogger("rest")
UTC = timezone.utc
BN = "https://fapi.binance.com"

async def _safe(name, coro):
    try:
        return await coro
    except Exception as e:
        log.warning("%s hatası: %s", name, e)
        return None

async def snap_binance(c):
    s = c + "USDT"
    oi, pi = await asyncio.gather(http.get(f"{BN}/fapi/v1/openInterest", {"symbol": s}), http.get(f"{BN}/fapi/v1/premiumIndex", {"symbol": s}))
    px = float(pi["markPrice"])
    extra = {}
    for key, path in [("ls_global", "globalLongShortAccountRatio"), ("ls_top_account", "topLongShortAccountRatio"),
                      ("ls_top_position", "topLongShortPositionRatio"), ("taker_buy_sell", "takerlongshortRatio")]:
        r = await _safe(path, http.get(f"{BN}/futures/data/{path}", {"symbol": s, "period": "5m", "limit": 1}))
        if r: extra[key] = float(r[0].get("longShortRatio") or r[0].get("buySellRatio"))
    return px, float(oi["openInterest"]) * px, float(pi["lastFundingRate"]), extra

async def snap_bybit(c):
    t = (await http.get("https://api.bybit.com/v5/market/tickers", {"category": "linear", "symbol": c + "USDT"}))["result"]["list"][0]
    extra = {}
    r = await _safe("bybit ls", http.get("https://api.bybit.com/v5/market/account-ratio", {"category": "linear", "symbol": c + "USDT", "period": "5min", "limit": 1}))
    if r and r["result"]["list"]:
        x = r["result"]["list"][0]; extra["ls_account"] = float(x["buyRatio"]) / max(1e-9, float(x["sellRatio"]))
    return float(t["markPrice"]), float(t["openInterestValue"]), float(t["fundingRate"]), extra

async def snap_okx(c):
    inst = f"{c}-USDT-SWAP"
    oi = (await http.get("https://www.okx.com/api/v5/public/open-interest", {"instType": "SWAP", "instId": inst}))["data"][0]
    fr = (await http.get("https://www.okx.com/api/v5/public/funding-rate", {"instId": inst}))["data"][0]
    mk = (await http.get("https://www.okx.com/api/v5/public/mark-price", {"instType": "SWAP", "instId": inst}))["data"][0]
    extra = {}
    r = await _safe("okx ls", http.get("https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio", {"ccy": c, "period": "5m"}))
    if r and r.get("data"): extra["ls_account"] = float(r["data"][0][1])
    return float(mk["markPx"]), float(oi["oiUsd"]), float(fr["fundingRate"]), extra

async def snap_bitget(c):
    t = (await http.get("https://api.bitget.com/api/v2/mix/market/ticker", {"symbol": c + "USDT", "productType": "usdt-futures"}))["data"][0]
    px = float(t["markPrice"]); return px, float(t["holdingAmount"]) * px, float(t["fundingRate"]), {}

async def snap_gate(c):
    t = await http.get(f"https://api.gateio.ws/api/v4/futures/usdt/contracts/{c}_USDT")
    px = float(t["mark_price"]); return px, float(t["position_size"]) * float(t["quanto_multiplier"]) * px, float(t["funding_rate"]), {}

_hl_cache = {}
async def snap_hyperliquid(c):
    async def load(): return await http.post("https://api.hyperliquid.xyz/info", {"type": "metaAndAssetCtxs"})
    meta, ctx = await http.cached("hl_meta", 60, load)
    x = ctx[[u["name"] for u in meta["universe"]].index(c)]
    px = float(x["markPx"]); return px, float(x["openInterest"]) * px, float(x["funding"]) * 8, {"premium": float(x["premium"] or 0)}

async def snap_deribit(c):
    if c not in ("BTC", "ETH"): return None
    r = (await http.get("https://www.deribit.com/api/v2/public/get_book_summary_by_instrument", {"instrument_name": f"{c}-PERPETUAL"}))["result"][0]
    return float(r["mark_price"]), float(r["open_interest"]), float(r.get("funding_8h") or 0), {}

SNAPS = {"binance": snap_binance, "bybit": snap_bybit, "okx": snap_okx, "bitget": snap_bitget, "gate": snap_gate,
         "hyperliquid": snap_hyperliquid, "deribit": snap_deribit}

async def poll_snapshots():
    while True:
        ts = datetime.fromtimestamp(int(time.time() // 300) * 300, UTC)
        rows = []
        for c in config.COINS:
            res = await asyncio.gather(*[_safe(f"{v} {c}", f(c)) for v, f in SNAPS.items()])
            for v, r in zip(SNAPS, res):
                if r: rows.append((ts, v, c, r[0], r[1], r[2], json.dumps(r[3])))
        if rows:
            async with db.pool.acquire() as cn:
                await cn.executemany("INSERT INTO deriv_snap (ts, venue, coin, price, oi_usd, funding_8h, extra) VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb) "
                                     "ON CONFLICT (coin, venue, ts) DO UPDATE SET price=EXCLUDED.price, oi_usd=EXCLUDED.oi_usd, funding_8h=EXCLUDED.funding_8h, extra=EXCLUDED.extra", rows)
        state.stream_health["rest_snapshots"] = {"last_msg": time.time(), "connected": True, "reconnects": 0, "msgs": len(rows)}
        await asyncio.sleep(300 - time.time() % 300 + 5)

async def options_summary(c):
    idx = (await http.get("https://www.deribit.com/api/v2/public/get_index_price", {"index_name": f"{c.lower()}_usd"}))["result"]["index_price"]
    bs = (await http.get("https://www.deribit.com/api/v2/public/get_book_summary_by_currency", {"currency": c, "kind": "option"}))["result"]
    by = collections.defaultdict(list)
    for o in bs:
        _, exp, k, typ = o["instrument_name"].split("-")
        by[exp].append((float(k), typ, o["open_interest"]))
    ed = lambda e: datetime.strptime(e, "%d%b%y").replace(tzinfo=UTC, hour=8)
    now = datetime.now(UTC)
    live = [e for e in by if ed(e) > now]
    exps = []
    for e in sorted(sorted(live, key=lambda e: -sum(r[2] for r in by[e]))[:6], key=ed):
        rows = by[e]; ks = sorted({r[0] for r in rows})
        pain = lambda S: sum(oi * max(0, S - k) if t == "C" else oi * max(0, k - S) for k, t, oi in rows)
        coi = sum(r[2] for r in rows if r[1] == "C"); poi = sum(r[2] for r in rows if r[1] == "P")
        exps.append({"expiry": ed(e).strftime("%Y-%m-%d"), "oi_usd": (coi + poi) * idx, "max_pain": min(ks, key=pain), "put_call": poi / max(1e-9, coi)})
    tc = sum(o["open_interest"] for o in bs if o["instrument_name"].endswith("-C")); tp = sum(o["open_interest"] for o in bs if o["instrument_name"].endswith("-P"))
    now_ms = int(time.time() * 1000)
    dv = (await http.get("https://www.deribit.com/api/v2/public/get_volatility_index_data",
                         {"currency": c, "start_timestamp": now_ms - 2 * 86400000, "end_timestamp": now_ms, "resolution": 3600}))["result"]["data"]
    return {"index": idx, "oi_usd": (tc + tp) * idx, "put_call": tp / max(1e-9, tc), "dvol": dv[-1][4] if dv else None,
            "dvol_24h_ago": (dv[-25][4] if len(dv) > 25 else dv[0][4]) if dv else None, "expiries": exps}

async def poll_options():
    while True:
        for c in [c for c in config.COINS if c in ("BTC", "ETH")]:
            r = await _safe(f"options {c}", options_summary(c))
            if r:
                async with db.pool.acquire() as cn:
                    await cn.execute("INSERT INTO options_snap (ts, coin, data) VALUES (date_trunc('minute', now()), $1, $2::jsonb) ON CONFLICT DO NOTHING", c, json.dumps(r))
        await asyncio.sleep(900)

async def flush_buckets():
    while True:
        await asyncio.sleep(10)
        closed = state.pop_closed_buckets()
        if not closed: continue
        rows = [(datetime.fromtimestamp(k[3], UTC), k[1], k[2], k[0], b[0], b[1], b[2], b[3], b[4], b[5], b[6]) for k, b in closed]
        try:
            async with db.pool.acquire() as cn:
                await cn.executemany("INSERT INTO trades_1m (ts, venue, market, coin, buy_usd, sell_usd, n, open, high, low, close) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) "
                                     "ON CONFLICT (coin, venue, market, ts) DO UPDATE SET buy_usd=trades_1m.buy_usd+EXCLUDED.buy_usd, sell_usd=trades_1m.sell_usd+EXCLUDED.sell_usd, "
                                     "n=trades_1m.n+EXCLUDED.n, high=GREATEST(trades_1m.high,EXCLUDED.high), low=LEAST(trades_1m.low,EXCLUDED.low), close=EXCLUDED.close", rows)
        except Exception as e:
            log.warning("kova yazma hatası: %s", e)

async def daily_cleanup():
    while True:
        await _safe("cleanup", db.cleanup())
        await asyncio.sleep(86400)

ALL = [poll_snapshots, poll_options, flush_buckets, daily_cleanup]
