"""Borsa WebSocket akışları: işlemler (balina, CVD) ve likidasyonlar."""
import asyncio, json, logging, time
from datetime import datetime, timezone
import aiohttp
from . import config, db, state

log = logging.getLogger("ws")
UTC = timezone.utc

async def save_liq(venue, coin, side, price, qty, ts_ms):
    usd = price * qty
    state.recent_liq.append((ts_ms / 1000, coin, usd, side))
    async with db.pool.acquire() as c:
        await c.execute("INSERT INTO liquidations (ts, venue, coin, side, price, qty, usd) VALUES ($1,$2,$3,$4,$5,$6,$7)",
                        datetime.fromtimestamp(ts_ms / 1000, UTC), venue, coin, side, price, qty, usd)
    if usd >= config.ALERT_LIQ_USD * config.alert_scale(coin):
        state.alerts.append({"type": "liq", "venue": venue, "coin": coin, "side": side, "price": price, "usd": usd, "ts": ts_ms / 1000})

async def trade(coin, venue, market, ts_ms, price, qty, is_buy):
    usd = price * qty
    state.add_trade(coin, venue, market, ts_ms, price, usd, is_buy)
    if usd >= config.whale_threshold(coin):
        async with db.pool.acquire() as c:
            await c.execute("INSERT INTO whale_trades (ts, venue, market, coin, side, price, usd) VALUES ($1,$2,$3,$4,$5,$6,$7)",
                            datetime.fromtimestamp(ts_ms / 1000, UTC), venue, market, coin, "buy" if is_buy else "sell", price, usd)
        if usd >= config.ALERT_WHALE_USD * config.alert_scale(coin):
            state.alerts.append({"type": "whale", "venue": venue, "market": market, "coin": coin, "side": "buy" if is_buy else "sell",
                                 "price": price, "usd": usd, "ts": ts_ms / 1000})

async def run_stream(name, url, on_open, on_msg, ping=None, ping_every=20):
    """Kopan bağlantıyı üstel bekleme ile yeniden kuran genel WebSocket döngüsü."""
    backoff = 1
    h = state.stream_health.setdefault(name, {"last_msg": 0, "connected": False, "reconnects": 0, "msgs": 0})
    while True:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.ws_connect(url, heartbeat=30, max_msg_size=0) as ws:
                    log.info("%s bağlandı", name); h["connected"] = True; backoff = 1
                    if on_open: await on_open(ws)
                    last_ping = time.time()
                    while True:
                        try:
                            msg = await ws.receive(timeout=ping_every)
                        except asyncio.TimeoutError:
                            msg = None
                        if ping and time.time() - last_ping >= ping_every:
                            await ping(ws); last_ping = time.time()
                        if msg is None: continue
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            if msg.data in ("pong", "ping"): continue
                            state.touch(name)
                            try:
                                await on_msg(ws, json.loads(msg.data))
                            except Exception as e:  # tek mesaj hatası akışı durdurmasın
                                log.warning("%s mesaj hatası: %s", name, e)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                            break
        except Exception as e:
            log.warning("%s bağlantı hatası: %s", name, e)
        h["connected"] = False; h["reconnects"] += 1
        await asyncio.sleep(backoff); backoff = min(backoff * 2, 60)

def sym_usdt(c): return c.lower() + "usdt"

# ---- Binance
async def binance_futures():
    streams = "/".join([f"{sym_usdt(c)}@aggTrade" for c in config.COINS] + ["!forceOrder@arr"])
    m = {c + "USDT": c for c in config.COINS}
    async def on_msg(ws, d):
        d = d.get("data", d)
        if d.get("e") == "aggTrade" and d["s"] in m:
            await trade(m[d["s"]], "binance", "perp", d["T"], float(d["p"]), float(d["q"]), not d["m"])
        elif d.get("e") == "forceOrder":
            o = d["o"]
            if o["s"] in m:
                await save_liq("binance", m[o["s"]], "long" if o["S"] == "SELL" else "short", float(o["ap"] or o["p"]), float(o["z"] or o["q"]), o["T"])
    await run_stream("binance_perp", f"wss://fstream.binance.com/stream?streams={streams}", None, on_msg)

async def binance_spot():
    streams = "/".join(f"{sym_usdt(c)}@aggTrade" for c in config.COINS)
    m = {c + "USDT": c for c in config.COINS}
    async def on_msg(ws, d):
        d = d.get("data", d)
        if d.get("e") == "aggTrade" and d["s"] in m:
            await trade(m[d["s"]], "binance", "spot", d["T"], float(d["p"]), float(d["q"]), not d["m"])
    await run_stream("binance_spot", f"wss://stream.binance.com:9443/stream?streams={streams}", None, on_msg)

# ---- Bybit
async def bybit():
    m = {c + "USDT": c for c in config.COINS}
    args = [f"publicTrade.{c}USDT" for c in config.COINS] + [f"allLiquidation.{c}USDT" for c in config.COINS]
    async def on_open(ws): await ws.send_json({"op": "subscribe", "args": args})
    async def ping(ws): await ws.send_json({"op": "ping"})
    async def on_msg(ws, d):
        topic = d.get("topic", "")
        if topic.startswith("publicTrade."):
            for t in d["data"]:
                if t["s"] in m: await trade(m[t["s"]], "bybit", "perp", int(t["T"]), float(t["p"]), float(t["v"]), t["S"] == "Buy")
        elif topic.startswith("allLiquidation."):
            for t in d["data"]:
                # Bybit: S=Buy uzun pozisyonun likide olduğunu bildirir
                if t["s"] in m: await save_liq("bybit", m[t["s"]], "long" if t["S"] == "Buy" else "short", float(t["p"]), float(t["v"]), int(t["T"]))
    await run_stream("bybit", "wss://stream.bybit.com/v5/public/linear", on_open, on_msg, ping, 20)

# ---- OKX
async def okx():
    inst = {f"{c}-USDT-SWAP": c for c in config.COINS if c in config.OKX_CTVAL}
    args = [{"channel": "trades", "instId": i} for i in inst] + [{"channel": "liquidation-orders", "instType": "SWAP"}]
    async def on_open(ws): await ws.send_json({"op": "subscribe", "args": args})
    async def ping(ws): await ws.send_str("ping")
    async def on_msg(ws, d):
        ch = d.get("arg", {}).get("channel")
        if ch == "trades" and "data" in d:
            for t in d["data"]:
                c = inst.get(t["instId"])
                if c: await trade(c, "okx", "perp", int(t["ts"]), float(t["px"]), float(t["sz"]) * config.OKX_CTVAL[c], t["side"] == "buy")
        elif ch == "liquidation-orders" and "data" in d:
            for blk in d["data"]:
                c = inst.get(blk.get("instId"))
                if not c: continue
                for e in blk.get("details", []):
                    side = e.get("posSide") or ("long" if e.get("side") == "sell" else "short")
                    await save_liq("okx", c, "long" if side == "long" else "short", float(e["bkPx"]), float(e["sz"]) * config.OKX_CTVAL[c], int(e["ts"]))
    await run_stream("okx", "wss://ws.okx.com/ws/v5/public", on_open, on_msg, ping, 25)

# ---- Coinbase (spot)
async def coinbase():
    prods = {f"{c}-USD": c for c in config.COINS}
    async def on_open(ws): await ws.send_json({"type": "subscribe", "product_ids": list(prods), "channels": ["matches"]})
    async def on_msg(ws, d):
        if d.get("type") == "match" and d.get("product_id") in prods:
            ts = datetime.fromisoformat(d["time"].replace("Z", "+00:00")).timestamp() * 1000
            # Coinbase'de "side" maker tarafıdır: maker sell = taker alım
            await trade(prods[d["product_id"]], "coinbase", "spot", ts, float(d["price"]), float(d["size"]), d["side"] == "sell")
    await run_stream("coinbase", "wss://ws-feed.exchange.coinbase.com", on_open, on_msg)

# ---- Hyperliquid
async def hyperliquid():
    async def on_open(ws):
        for c in config.COINS:
            await ws.send_json({"method": "subscribe", "subscription": {"type": "trades", "coin": c}})
    async def ping(ws): await ws.send_json({"method": "ping"})
    async def on_msg(ws, d):
        if d.get("channel") == "trades":
            for t in d["data"]:
                if t["coin"] in config.COINS:
                    await trade(t["coin"], "hyperliquid", "perp", int(t["time"]), float(t["px"]), float(t["sz"]), t["side"] == "B")
    await run_stream("hyperliquid", "wss://api.hyperliquid.xyz/ws", on_open, on_msg, ping, 45)

ALL = [binance_futures, binance_spot, bybit, okx, coinbase, hyperliquid]
