"""Periyodik REST toplayıcılar: OI, funding, long/short, opsiyonlar; dakikalık kovaları veritabanına yazar."""
import asyncio, collections, json, logging, time
from datetime import datetime, timezone
from . import config, db, http, option_metrics, state

log = logging.getLogger("rest")
UTC = timezone.utc
BN = "https://fapi.binance.com"

async def _safe(name, coro):
    try:
        return await coro
    except Exception as e:
        log.warning("%s hatası: %s", name, e)
        return None

def to_8h(rate, interval_hours):
    """Farklı ödeme aralıklarını (1s, 4s, 8s) karşılaştırılabilir 8 saatlik orana çevirir."""
    return float(rate) * 8 / (interval_hours or 8)

async def _binance_intervals():
    async def load():
        return {x["symbol"]: float(x["fundingIntervalHours"]) for x in await http.get(f"{BN}/fapi/v1/fundingInfo")}
    return await http.cached("bn_funding_info", 3600, load)

async def _bybit_interval(sym):
    async def load():
        r = await http.get("https://api.bybit.com/v5/market/instruments-info", {"category": "linear", "symbol": sym})
        return float(r["result"]["list"][0]["fundingInterval"]) / 60
    return await http.cached(f"bybit_fi:{sym}", 3600, load)

async def _bitget_interval(sym):
    async def load():
        r = await http.get("https://api.bitget.com/api/v2/mix/market/current-fund-rate", {"symbol": sym, "productType": "usdt-futures"})
        return float(r["data"][0].get("fundingRateInterval") or 8)
    return await http.cached(f"bitget_fi:{sym}", 3600, load)

async def snap_binance(c):
    s = c + "USDT"
    oi, pi = await asyncio.gather(http.get(f"{BN}/fapi/v1/openInterest", {"symbol": s}), http.get(f"{BN}/fapi/v1/premiumIndex", {"symbol": s}))
    px = float(pi["markPrice"])
    extra = {}
    for key, path in [("ls_global", "globalLongShortAccountRatio"), ("ls_top_account", "topLongShortAccountRatio"),
                      ("ls_top_position", "topLongShortPositionRatio"), ("taker_buy_sell", "takerlongshortRatio")]:
        r = await _safe(path, http.get(f"{BN}/futures/data/{path}", {"symbol": s, "period": "5m", "limit": 1}))
        if r: extra[key] = float(r[0].get("longShortRatio") or r[0].get("buySellRatio"))
    iv = (await _safe("binance fundingInfo", _binance_intervals()) or {}).get(s, 8)
    extra["funding_interval_h"] = iv
    extra["basis_pct"] = (px / float(pi["indexPrice"]) - 1) * 100
    extra["next_funding_utc_ms"] = pi.get("nextFundingTime")
    return px, float(oi["openInterest"]) * px, to_8h(pi["lastFundingRate"], iv), extra

async def snap_bybit(c):
    t = (await http.get("https://api.bybit.com/v5/market/tickers", {"category": "linear", "symbol": c + "USDT"}))["result"]["list"][0]
    extra = {}
    r = await _safe("bybit ls", http.get("https://api.bybit.com/v5/market/account-ratio", {"category": "linear", "symbol": c + "USDT", "period": "5min", "limit": 1}))
    if r and r["result"]["list"]:
        x = r["result"]["list"][0]; extra["ls_account"] = float(x["buyRatio"]) / max(1e-9, float(x["sellRatio"]))
    iv = await _safe("bybit fundingInterval", _bybit_interval(c + "USDT")) or 8
    extra["funding_interval_h"] = iv
    extra["basis_pct"] = (float(t["markPrice"]) / float(t["indexPrice"]) - 1) * 100
    extra["next_funding_utc_ms"] = t.get("nextFundingTime")
    return float(t["markPrice"]), float(t["openInterestValue"]), to_8h(t["fundingRate"], iv), extra

async def snap_okx(c):
    inst = f"{c}-USDT-SWAP"
    oi = (await http.get("https://www.okx.com/api/v5/public/open-interest", {"instType": "SWAP", "instId": inst}))["data"][0]
    fr = (await http.get("https://www.okx.com/api/v5/public/funding-rate", {"instId": inst}))["data"][0]
    mk = (await http.get("https://www.okx.com/api/v5/public/mark-price", {"instType": "SWAP", "instId": inst}))["data"][0]
    extra = {}
    r = await _safe("okx ls", http.get("https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio", {"ccy": c, "period": "5m"}))
    if r and r.get("data"): extra["ls_account"] = float(r["data"][0][1])
    iv = (int(fr["nextFundingTime"]) - int(fr["fundingTime"])) / 3_600_000 if fr.get("nextFundingTime") and fr.get("fundingTime") else 8
    extra["funding_interval_h"] = iv
    return float(mk["markPx"]), float(oi["oiUsd"]), to_8h(fr["fundingRate"], iv), extra

async def snap_bitget(c):
    t = (await http.get("https://api.bitget.com/api/v2/mix/market/ticker", {"symbol": c + "USDT", "productType": "usdt-futures"}))["data"][0]
    iv = await _safe("bitget fundingInterval", _bitget_interval(c + "USDT")) or 8
    px = float(t["markPrice"]); return px, float(t["holdingAmount"]) * px, to_8h(t["fundingRate"], iv), {"funding_interval_h": iv}

async def snap_gate(c):
    t = await http.get(f"https://api.gateio.ws/api/v4/futures/usdt/contracts/{c}_USDT")
    iv = float(t.get("funding_interval") or 28800) / 3600
    px = float(t["mark_price"]); return px, float(t["position_size"]) * float(t["quanto_multiplier"]) * px, to_8h(t["funding_rate"], iv), {"funding_interval_h": iv}

async def snap_hyperliquid(c):
    async def load(): return await http.post("https://api.hyperliquid.xyz/info", {"type": "metaAndAssetCtxs"})
    meta, ctx = await http.cached("hl_meta", 60, load)
    x = ctx[[u["name"] for u in meta["universe"]].index(c)]
    px = float(x["markPx"]); return px, float(x["openInterest"]) * px, to_8h(x["funding"], 1), {"premium": float(x["premium"] or 0), "funding_interval_h": 1}

async def snap_deribit(c):
    if c not in ("BTC", "ETH"): return None
    r = (await http.get("https://www.deribit.com/api/v2/public/get_book_summary_by_instrument", {"instrument_name": f"{c}-PERPETUAL"}))["result"][0]
    return float(r["mark_price"]), float(r["open_interest"]), float(r["funding_8h"]) if r.get("funding_8h") is not None else None, {}

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
        previous = state.stream_health.get("rest_snapshots", {})
        state.stream_health["rest_snapshots"] = {"last_msg": time.time() if rows else previous.get("last_msg", 0),
                                                  "connected": bool(rows), "reconnects": 0, "msgs": len(rows)}
        await asyncio.sleep(300 - time.time() % 300 + 5)

async def options_summary(c):
    idx = (await http.get("https://www.deribit.com/api/v2/public/get_index_price", {"index_name": f"{c.lower()}_usd"}))["result"]["index_price"]
    bs = (await http.get("https://www.deribit.com/api/v2/public/get_book_summary_by_currency", {"currency": c, "kind": "option"}))["result"]
    by = collections.defaultdict(list)
    now = datetime.now(UTC)
    for o in bs:
        name = o["instrument_name"]
        _, exp, strike, side = name.split("-")
        expiry = datetime.strptime(exp, "%d%b%y").replace(tzinfo=UTC, hour=8)
        if expiry > now and o["open_interest"] > 0:
            by[expiry].append({"name": name, "strike": float(strike), "side": side,
                               "oi": float(o["open_interest"]), "iv": float(o.get("mark_iv") or 0) / 100,
                               "forward": float(o.get("underlying_price") or 0),
                               "rate": float(o.get("interest_rate") or 0)})
    exps = []
    ranked = sorted(by, key=lambda e: -sum(r["oi"] for r in by[e]))[:6]
    for expiry in sorted(ranked):
        metrics = option_metrics.expiry_metrics(by[expiry], idx, (expiry - now).total_seconds())
        exps.append({"expiry": expiry.strftime("%Y-%m-%d"), **metrics})
    chain = [row for rows in by.values() for row in rows]
    tc = sum(row["oi"] for row in chain if row["side"] == "C")
    tp = sum(row["oi"] for row in chain if row["side"] == "P")

    async def ticker(row):
        response = await http.get("https://www.deribit.com/api/v2/public/ticker", {"instrument_name": row["name"]})
        result = response["result"]
        return {"instrument": row["name"], "timestamp_ms": result["timestamp"],
                "greeks": result.get("greeks"), "mark_iv": result.get("mark_iv")}

    largest = sorted(chain, key=lambda row: -row["oi"])[:4]
    exchange_greeks = []
    for row in largest:
        exchange_greeks.append(await _safe("Deribit ticker", ticker(row)))
        await asyncio.sleep(1.0)  # Deribit genel uçları için istek sınırına takılmamak üzere
    now_ms = int(time.time() * 1000)
    dv = (await http.get("https://www.deribit.com/api/v2/public/get_volatility_index_data",
                         {"currency": c, "start_timestamp": now_ms - 2 * 86400000, "end_timestamp": now_ms, "resolution": 3600}))["result"]["data"]
    return {"index": idx, "oi_usd": (tc + tp) * idx, "put_call": tp / max(1e-9, tc), "dvol": dv[-1][4] if dv else None,
            "dvol_24h_ago": (dv[-25][4] if len(dv) > 25 else dv[0][4]) if dv else None, "expiries": exps, "exchange_greeks": [g for g in exchange_greeks if g and g["greeks"]],
            "greeks_model": "Black-76 using Deribit mark IV and forward",
            "gex_method": "Gamma × base OI × index² × 1%; signed proxy assumes calls positive and puts negative. Dealer positions are unknown."}

async def poll_options():
    while True:
        for c in [c for c in config.COINS if c in ("BTC", "ETH")]:
            r = await _safe(f"options {c}", options_summary(c))
            if r:
                async with db.pool.acquire() as cn:
                    await cn.execute("INSERT INTO options_snap (ts, coin, data) VALUES (date_trunc('minute', now()), $1, $2::jsonb) ON CONFLICT DO NOTHING", c, json.dumps(r))
        await asyncio.sleep(900)

async def daily_cleanup():
    while True:
        await _safe("cleanup", db.cleanup())
        await asyncio.sleep(86400)

ALL = [poll_snapshots, poll_options, daily_cleanup]
