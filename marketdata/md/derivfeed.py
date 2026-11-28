"""Funding ve open interest için public akışlar.

Binance mark ve funding soketten gelir. Binance open interest public sokette yoktur;
30 saniyelik public REST ile okunur ve taşıma alanı `rest` diye yazılır.
Bybit ticker ve Hyperliquid activeAssetCtx hem funding hem OI taşır.
Beş dakikalık deriv_snap geçmişi durur. Canlı değer, taze ise onun üstüne yazılır.
Emir soketi yoktur.
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from . import config, db, http, rest, state, ws

log = logging.getLogger("derivfeed")
UTC = timezone.utc
BN = "https://fapi.binance.com"
INTERVALS = {"binance": {}, "bybit": {}}


async def refresh_intervals():
    while True:
        try:
            INTERVALS["binance"] = await rest._binance_intervals()
        except Exception as exc:
            log.warning("Binance funding aralığı alınamadı: %s", type(exc).__name__)
        for coin in config.COINS:
            try:
                INTERVALS["bybit"][coin] = await rest._bybit_interval(coin + "USDT")
            except Exception as exc:
                log.warning("Bybit funding aralığı %s: %s", coin, type(exc).__name__)
        await asyncio.sleep(3600)


def _funding(rate, hours):
    if rate is None or hours is None:
        return None
    return rest.to_8h(rate, hours)


async def binance_mark():
    if not INTERVALS["binance"]:
        try:
            INTERVALS["binance"] = await rest._binance_intervals()
        except Exception:
            INTERVALS["binance"] = {}
    streams = "/".join(c.lower() + "usdt@markPrice@1s" for c in config.COINS)
    symbols = {c + "USDT": c for c in config.COINS}

    async def on_msg(socket, message):
        data = message.get("data", message)
        if data.get("e") != "markPriceUpdate" or data.get("s") not in symbols:
            return
        coin = symbols[data["s"]]
        hours = INTERVALS["binance"].get(data["s"], 8)
        state.note_deriv("binance", coin, price=data.get("p"), funding_8h=_funding(data.get("r"), hours),
                         transport="websocket", extra={"funding_interval_h": hours, "index_price": data.get("i")})

    await ws.run_stream("deriv_binance_mark", "wss://fstream.binance.com/market/stream?streams=" + streams, None, on_msg)


async def binance_oi():
    """Public open-interest REST. Binance bu alanı sokette yayınlamaz."""
    while True:
        for coin in config.COINS:
            try:
                row = await http.get(f"{BN}/fapi/v1/openInterest", {"symbol": coin + "USDT"})
                mark = state.live_deriv.get(("binance", coin), {}).get("price")
                if mark and row.get("openInterest") is not None:
                    state.note_deriv("binance", coin, oi_usd=float(row["openInterest"]) * float(mark), transport="rest")
            except Exception as exc:
                log.warning("Binance OI %s: %s", coin, type(exc).__name__)
        await asyncio.sleep(30)


async def bybit_ticker():
    symbols = {c + "USDT": c for c in config.COINS}
    cache = {}

    async def on_open(socket):
        await socket.send_json({"op": "subscribe", "args": ["tickers." + s for s in symbols]})

    async def ping(socket):
        await socket.send_json({"op": "ping"})

    async def on_msg(socket, message):
        if not str(message.get("topic", "")).startswith("tickers."):
            return
        data = message.get("data") or {}
        sym = data.get("symbol")
        if sym not in symbols:
            return
        row = cache.setdefault(sym, {})
        if message.get("type") == "snapshot":
            row.clear()
        row.update({k: v for k, v in data.items() if v not in (None, "")})
        coin = symbols[sym]
        mark = row.get("markPrice")
        oi = row.get("openInterestValue")
        if oi is None and row.get("openInterest") and mark:
            oi = float(row["openInterest"]) * float(mark)
        hours = INTERVALS["bybit"].get(coin)
        state.note_deriv("bybit", coin, price=mark, oi_usd=oi, funding_8h=_funding(row.get("fundingRate"), hours),
                         transport="websocket", extra={"funding_interval_h": hours} if hours else None)

    await ws.run_stream("deriv_bybit", "wss://stream.bybit.com/v5/public/linear", on_open, on_msg, ping)


async def hyperliquid_ctx():
    async def on_open(socket):
        for coin in config.COINS:
            await socket.send_json({"method": "subscribe", "subscription": {"type": "activeAssetCtx", "coin": coin}})

    async def ping(socket):
        await socket.send_json({"method": "ping"})

    async def on_msg(socket, message):
        if message.get("channel") != "activeAssetCtx":
            return
        data = message.get("data") or {}
        ctx = data.get("ctx") or data
        coin = data.get("coin") or ctx.get("coin")
        if coin not in config.COINS or not ctx.get("markPx"):
            return
        mark = float(ctx["markPx"])
        oi = float(ctx["openInterest"]) * mark if ctx.get("openInterest") is not None else None
        extra = {"funding_interval_h": 1, "premium": ctx.get("premium"), "oracle_px": ctx.get("oraclePx"),
                 "impact_pxs": ctx.get("impactPxs")}
        state.note_deriv("hyperliquid", coin, price=mark, oi_usd=oi, funding_8h=_funding(ctx.get("funding"), 1),
                         transport="websocket", extra=extra)

    await ws.run_stream("deriv_hyperliquid", "wss://api.hyperliquid.xyz/ws", on_open, on_msg, ping, 45)


def overlay_live(venues, live, coin, now):
    """Taze soket alanını REST satırının üstüne koyar. Bayat veya boş alan DB değerini silmez."""
    merged = {k: dict(v) for k, v in venues.items()}
    for (venue, name), row in live.items():
        if name != coin:
            continue
        current = merged.get(venue, {"snapshot_utc": None, "age_seconds": 9999, "price": None, "oi_usd": None, "funding_8h": None})
        ages = []
        for field, ts_key, transport_key in (
            ("price", "price_ts", "price_transport"),
            ("oi_usd", "oi_ts", "oi_transport"),
            ("funding_8h", "funding_ts", "funding_transport"),
        ):
            ts = row.get(ts_key)
            if row.get(field) is None or ts is None or now - ts > 180:
                continue
            current[field] = row[field]
            current[transport_key] = row.get(transport_key)
            ages.append(now - ts)
        if not ages:
            continue
        current["age_seconds"] = int(max(ages))
        current["snapshot_utc"] = datetime.fromtimestamp(now - min(ages), UTC).isoformat()
        current["live"] = True
        if row.get("extra"):
            current.update({k: v for k, v in row["extra"].items() if v is not None})
        merged[venue] = current
    return merged


async def persist_live():
    while True:
        await asyncio.sleep(60)
        now = time.time()
        ts = datetime.fromtimestamp(int(now // 300) * 300, UTC)
        rows = []
        for (venue, coin), row in list(state.live_deriv.items()):
            fresh = [row.get(key) for key in ("price_ts", "oi_ts", "funding_ts") if row.get(key) and now - row[key] <= 180]
            if not fresh:
                continue
            if row.get("price") is None and row.get("oi_usd") is None and row.get("funding_8h") is None:
                continue
            extra = dict(row.get("extra") or {})
            extra["transport"] = row.get("oi_transport") or row.get("funding_transport") or row.get("price_transport")
            rows.append((ts, venue, coin, row.get("price"), row.get("oi_usd"), row.get("funding_8h"), json.dumps(extra)))
        if not rows or db.pool is None:
            continue
        try:
            async with db.pool.acquire() as conn:
                await conn.executemany(
                    """INSERT INTO deriv_snap (ts, venue, coin, price, oi_usd, funding_8h, extra)
                       VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)
                       ON CONFLICT (coin, venue, ts) DO UPDATE SET
                         price=COALESCE(EXCLUDED.price, deriv_snap.price),
                         oi_usd=COALESCE(EXCLUDED.oi_usd, deriv_snap.oi_usd),
                         funding_8h=COALESCE(EXCLUDED.funding_8h, deriv_snap.funding_8h),
                         extra=deriv_snap.extra || EXCLUDED.extra""", rows)
        except Exception as exc:
            log.warning("canlı türev yazılamadı: %s", type(exc).__name__)


ALL = [binance_mark, binance_oi, bybit_ticker, hyperliquid_ctx, refresh_intervals, persist_live]
