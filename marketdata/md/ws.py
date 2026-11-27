"""Borsa WebSocket akışları: işlemler (balina, CVD) ve likidasyonlar."""
import asyncio, json, logging, time
from datetime import datetime, timezone
import aiohttp
from . import config, db, state, ingest

log = logging.getLogger("ws")
UTC = timezone.utc

class ResyncRequired(Exception):
    """Defter zinciri doğrulanamadı; yeni bağlantı ve snapshot gerekir."""

async def save_liq(venue, coin, side, price, qty, ts_ms):
    await ingest.submit("liquidation", coin, venue, "perp", ts_ms, price, qty, side)

async def trade(coin, venue, market, ts_ms, price, qty, is_buy, native_id=None):
    await ingest.submit("trade", coin, venue, market, ts_ms, price, qty,
                        "buy" if is_buy else "sell", native_id)

async def run_stream(name, url, on_open, on_msg, ping=None, ping_every=20, on_disconnect=None):
    """Kopan bağlantıyı üstel bekleme ile yeniden kuran genel WebSocket döngüsü."""
    backoff = 1
    h = state.stream_health.setdefault(name, {"last_msg": 0, "connected": False, "reconnects": 0, "msgs": 0})
    while True:
        try:
            async with aiohttp.ClientSession(trust_env=True) as s:
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
                        if msg is None:
                            if time.time() - h["last_msg"] > max(120, ping_every * 3):
                                break
                            continue
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            if msg.data in ("pong", "ping"): continue
                            state.touch(name)
                            try:
                                await on_msg(ws, json.loads(msg.data))
                            except ResyncRequired:
                                break
                            except Exception as e:  # tek mesaj hatası akışı durdurmasın
                                log.warning("%s mesaj hatası: %s", name, e)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                            break
        except Exception as e:
            log.warning("%s bağlantı hatası: %s", name, e)
        if on_disconnect:
            on_disconnect()
        h["connected"] = False; h["reconnects"] += 1
        await asyncio.sleep(backoff); backoff = min(backoff * 2, 60)

def sym_usdt(c): return c.lower() + "usdt"

# ---- Binance
async def run_rotating(name, urls, on_msg, silent_after=45):
    """Birden fazla aday adresi dener: bağlanıp silent_after saniye mesaj alamazsa sıradakine geçer."""
    h = state.stream_health.setdefault(name, {"last_msg": 0, "connected": False, "reconnects": 0, "msgs": 0, "url": None})
    i, backoff = 0, 1
    proven = None  # veri geldiği görülen adres: bir kez çalıştıysa sessizlikte ondan ayrılma
    while True:
        url = proven or urls[i % len(urls)]
        got = False
        try:
            async with aiohttp.ClientSession(trust_env=True) as s:
                async with s.ws_connect(url, heartbeat=30, max_msg_size=0) as ws:
                    log.info("%s bağlandı: %s", name, url); h["connected"] = True; h["url"] = url
                    while True:
                        try:
                            msg = await ws.receive(timeout=silent_after if not (got or proven) else max(120, silent_after))
                        except asyncio.TimeoutError:
                            log.info("%s %s adresinden veri gelmedi, yeniden bağlanılıyor", name, url); break
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            got = True; proven = url; backoff = 1; state.touch(name)
                            try: await on_msg(ws, json.loads(msg.data))
                            except Exception as e: log.warning("%s mesaj hatası: %s", name, e)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                            break
        except Exception as e:
            log.warning("%s bağlantı hatası (%s): %s", name, url, e)
        h["connected"] = False; h["reconnects"] += 1
        if not got and not proven: i += 1
        await asyncio.sleep(backoff); backoff = min(backoff * 2, 60)

BN_FUT_BASES = ["wss://fstream.binance.com/market", "wss://fstream.binance.com", "wss://fstream.binance.com/public"]

async def binance_futures_trades():
    streams = "/".join(f"{sym_usdt(c)}@aggTrade" for c in config.COINS)
    m = {c + "USDT": c for c in config.COINS}
    async def on_msg(ws, d):
        d = d.get("data", d)
        if d.get("e") == "aggTrade" and d["s"] in m:
            await trade(m[d["s"]], "binance", "perp", d["T"], float(d["p"]), float(d["q"]), not d["m"], d["a"])
    await run_rotating("binance_perp", [f"{b}/stream?streams={streams}" for b in BN_FUT_BASES], on_msg)

async def binance_futures_liq():
    m = {c + "USDT": c for c in config.COINS}
    async def on_msg(ws, d):
        d = d.get("data", d)
        if d.get("e") == "forceOrder":
            o = d["o"]
            if o["s"] in m:
                await save_liq("binance", m[o["s"]], "long" if o["S"] == "SELL" else "short", float(o["ap"] or o["p"]), float(o["z"] or o["q"]), o["T"])
    # Likidasyon akışı seyrek olabilir: sessizlik eşiği daha uzun
    await run_rotating("binance_liq", [f"{b}/ws/!forceOrder@arr" for b in BN_FUT_BASES], on_msg, silent_after=1800)

async def binance_spot():
    streams = "/".join(f"{sym_usdt(c)}@aggTrade" for c in config.COINS)
    m = {c + "USDT": c for c in config.COINS}
    async def on_msg(ws, d):
        d = d.get("data", d)
        if d.get("e") == "aggTrade" and d["s"] in m:
            await trade(m[d["s"]], "binance", "spot", d["T"], float(d["p"]), float(d["q"]), not d["m"], d["a"])
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
                if t["s"] in m: await trade(m[t["s"]], "bybit", "perp", int(t["T"]), float(t["p"]), float(t["v"]), t["S"] == "Buy", t.get("i"))
        elif topic.startswith("allLiquidation."):
            for t in d["data"]:
                # Bybit: S=Buy uzun pozisyonun likide olduğunu bildirir
                if t["s"] in m: await save_liq("bybit", m[t["s"]], "long" if t["S"] == "Buy" else "short", float(t["p"]), float(t["v"]), int(t["T"]))
    await run_stream("bybit", "wss://stream.bybit.com/v5/public/linear", on_open, on_msg, ping, 20)

# ---- OKX
async def load_okx_ctval():
    try:
        async with aiohttp.ClientSession(trust_env=True) as s:
            async with s.get("https://www.okx.com/api/v5/public/instruments", params={"instType": "SWAP"}) as r:
                for i in (await r.json())["data"]:
                    if i["instId"].endswith("-USDT-SWAP"): config.OKX_CTVAL[i["instId"].split("-")[0]] = float(i["ctVal"])
    except Exception as e:
        log.warning("OKX ctVal yüklenemedi: %s", e)

async def okx():
    await load_okx_ctval()
    inst = {f"{c}-USDT-SWAP": c for c in config.COINS if c in config.OKX_CTVAL}
    args = [{"channel": "trades", "instId": i} for i in inst] + [{"channel": "liquidation-orders", "instType": "SWAP"}]
    async def on_open(ws): await ws.send_json({"op": "subscribe", "args": args})
    async def ping(ws): await ws.send_str("ping")
    async def on_msg(ws, d):
        ch = d.get("arg", {}).get("channel")
        if ch == "trades" and "data" in d:
            for t in d["data"]:
                c = inst.get(t["instId"])
                if c: await trade(c, "okx", "perp", int(t["ts"]), float(t["px"]), float(t["sz"]) * config.OKX_CTVAL[c], t["side"] == "buy", t.get("tradeId"))
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
            await trade(prods[d["product_id"]], "coinbase", "spot", ts, float(d["price"]), float(d["size"]), d["side"] == "sell", d.get("trade_id"))
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
                    await trade(t["coin"], "hyperliquid", "perp", int(t["time"]), float(t["px"]), float(t["sz"]), t["side"] == "B", t.get("tid", t.get("hash")))
    await run_stream("hyperliquid", "wss://api.hyperliquid.xyz/ws", on_open, on_msg, ping, 45)

async def bybit_spot():
    symbols = {f"{c}USDT": c for c in config.COINS}
    async def on_open(ws):
        await ws.send_json({"op": "subscribe", "args": [f"publicTrade.{s}" for s in symbols]})
    async def ping(ws):
        await ws.send_json({"op": "ping"})
    async def on_msg(ws, d):
        if d.get("topic", "").startswith("publicTrade."):
            for t in d.get("data", []):
                if t["s"] in symbols:
                    await trade(symbols[t["s"]], "bybit", "spot", int(t["T"]), float(t["p"]),
                                float(t["v"]), t["S"] == "Buy", t.get("i"))
    await run_stream("bybit_spot", "wss://stream.bybit.com/v5/public/spot", on_open, on_msg, ping)

async def okx_spot():
    symbols = {f"{c}-USDT": c for c in config.COINS}
    async def on_open(ws):
        await ws.send_json({"op": "subscribe", "args": [{"channel": "trades", "instId": s} for s in symbols]})
    async def ping(ws):
        await ws.send_str("ping")
    async def on_msg(ws, d):
        if d.get("arg", {}).get("channel") == "trades":
            for t in d.get("data", []):
                if t["instId"] in symbols:
                    await trade(symbols[t["instId"]], "okx", "spot", int(t["ts"]), float(t["px"]),
                                float(t["sz"]), t["side"] == "buy", t.get("tradeId"))
    await run_stream("okx_spot", "wss://ws.okx.com/ws/v5/public", on_open, on_msg, ping, 25)

ALL = [binance_futures_trades, binance_futures_liq, binance_spot, bybit, okx, coinbase,
       hyperliquid, bybit_spot, okx_spot]
