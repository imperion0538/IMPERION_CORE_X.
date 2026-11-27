"""HTTP API: Claude ve diğer istemciler için. /health dışındaki uçlar X-API-Key ister."""
import hmac, json, math, time
from datetime import timedelta
from aiohttp import web
from pathlib import Path
from . import analysis, card4k, charts, config, db, heatmap, orderbook, state, whales as whale_flow, reports

def _key_ok(req):
    if not config.API_KEY: return False
    k = req.headers.get("X-API-Key") or (req.headers.get("Authorization", "").removeprefix("Bearer ").strip())
    return bool(k) and hmac.compare_digest(k, config.API_KEY)

_hits: dict = {}

def _rate_ok(req, limit=60):
    """IP başına dakikada en fazla `limit` istek (anahtarsız erişim için)."""
    ip = (req.headers.get("X-Forwarded-For", "").split(",")[0].strip()) or (req.remote or "?")
    now = time.time(); q = [t for t in _hits.get(ip, []) if now - t < 60]
    if len(q) >= limit: _hits[ip] = q; return False
    q.append(now); _hits[ip] = q
    if len(_hits) > 5000: _hits.clear()
    return True

@web.middleware
async def auth(req, handler):
    if req.path in ("/", "/health", "/panel") or _key_ok(req):
        return await handler(req)
    if config.PUBLIC_READ and req.method == "GET":
        if not _rate_ok(req): return web.json_response({"error": "rate limited"}, status=429)
        return await handler(req)
    return web.json_response({"error": "unauthorized"}, status=401)

def _coin(req): return req.query.get("coin", "BTC").upper()
def _win(req, d="24h"): return req.query.get("window", d)
def j(data): return web.Response(text=json.dumps(data, default=str), content_type="application/json")

async def health(req):
    now = time.time()
    stale = [name for name, h in state.stream_health.items()
             if name != "binance_liq" and now - h["last_msg"] > (420 if name == "rest_snapshots" else 120)]
    data_status = "starting" if not state.stream_health else "degraded" if stale else "active"
    return j({"status": "ok", "data_status": data_status, "stale_streams": stale, "streams": {n: {"age_s": int(now - h["last_msg"]), "reconnects": h["reconnects"], "msgs": h["msgs"]} for n, h in state.stream_health.items()}})

async def an(req): return j(await analysis.analysis(_coin(req)))
async def live_report(req):
    coin, window = _coin(req), _win(req, "15m")
    if coin not in config.COINS or window not in whale_flow.WINDOWS:
        return web.json_response({"error": "Invalid monitored coin or window"}, status=400)
    try:
        low = float(req.query["min_price"]) if "min_price" in req.query else None
        high = float(req.query["max_price"]) if "max_price" in req.query else None
    except ValueError:
        return web.json_response({"error": "Invalid price range"}, status=400)
    if (any(v is not None and (not math.isfinite(v) or v <= 0) for v in (low, high))
            or (low is not None and high is not None and low > high)):
        return web.json_response({"error": "Invalid price range"}, status=400)
    return j(await reports.generate_report(coin, window=window, min_price=low, max_price=high))

async def heat(req):
    w = _win(req)
    if w not in heatmap.WINDOWS: return j({"error": f"window şunlardan biri olmalı: {list(heatmap.WINDOWS)}"})
    return j(await heatmap.model(_coin(req), w))
async def heat_png(req):
    c, w = _coin(req), _win(req)
    if w not in heatmap.WINDOWS: return web.json_response({"error": f"window şunlardan biri olmalı: {list(heatmap.WINDOWS)}"}, status=400)
    h = await heatmap.model(c, w)
    return web.Response(body=charts.heatmap_png(h, f"{c} tahmini likidasyon haritası · {w}"), content_type="image/png")
async def liq(req): return j(await heatmap.realized(_coin(req), _win(req)))
async def whales(req):
    secs = {"1h": 3600, "4h": 14400, "24h": 86400, "7d": 604800}.get(_win(req, "4h"), 14400)
    async with db.pool.acquire() as c:
        rows = await c.fetch("SELECT ts, venue, market, side, price, usd FROM whale_trades WHERE coin=$1 AND ts > now() - $2::interval AND usd >= $3 ORDER BY ts DESC LIMIT 500",
                             _coin(req), timedelta(seconds=secs), float(req.query.get("min_usd", 0)))
    return j([dict(r) for r in rows])
async def whale_report(req):
    coin, window = _coin(req), _win(req, "15m")
    if coin not in config.COINS or window not in whale_flow.WINDOWS:
        return web.json_response({"error": "Use a monitored coin and window: 5m, 15m, 1h, 4h"}, status=400)
    try:
        low = float(req.query["min_price"]) if "min_price" in req.query else None
        high = float(req.query["max_price"]) if "max_price" in req.query else None
        limit = int(req.query.get("limit", 20))
    except ValueError:
        return web.json_response({"error": "Invalid price or limit"}, status=400)
    if (any(x is not None and (not math.isfinite(x) or x <= 0) for x in (low, high))
            or (low is not None and high is not None and low > high) or not 1 <= limit <= 50):
        return web.json_response({"error": "Use a positive price range and limit between 1 and 50"}, status=400)
    return j(await whale_flow.report(coin, window, low, high, limit))

async def deriv(req): return j(await analysis.deriv(_coin(req)))
async def opts(req): return j(await analysis.options(_coin(req)))
async def alts(req): return j(await analysis.alts(req.query.get("base", "BTC").upper()))
async def cvd(req):
    secs = {"1h": 3600, "4h": 14400, "24h": 86400, "7d": 604800}.get(_win(req), 86400)
    async with db.pool.acquire() as c:
        rows = await c.fetch("SELECT date_trunc('hour', ts) h, market, sum(buy_usd) b, sum(sell_usd) s, avg(close) px FROM trades_1m WHERE coin=$1 AND ts > now() - $2::interval GROUP BY 1, 2 ORDER BY 1",
                             _coin(req), timedelta(seconds=secs))
    return j([{"hour": r["h"], "market": r["market"], "buy_usd": r["b"], "sell_usd": r["s"], "delta": r["b"] - r["s"], "avg_price": r["px"]} for r in rows])

PANEL = Path(__file__).with_name("static") / "panel.html"


def _hours(req, d=4, mx=720):
    try:
        return max(1, min(mx, int(req.query.get("hours", d))))
    except ValueError:
        return d


async def panel(req): return web.FileResponse(PANEL, headers={"Cache-Control": "no-cache"})
async def coins(req): return j({"coins": config.COINS})


async def depth(req):
    d = await orderbook.latest(_coin(req))
    return j(d) if d else web.json_response({"error": "henüz emir defteri görüntüsü yok"}, status=404)


async def depth_png(req):
    c, h = _coin(req), _hours(req, 4, 24)
    return web.Response(body=charts.orderbook_heatmap_png(await orderbook.history(c, h), f"{c} emir defteri ısı haritası · son {h} saat"), content_type="image/png")


async def cvd_png(req):
    c, h = _coin(req), _hours(req, 24)
    return web.Response(body=charts.cvd_png(await analysis.cvd_series(c, h), f"{c} CVD · son {h} saat"), content_type="image/png")


async def liq_png(req):
    c, w = _coin(req), _win(req)
    return web.Response(body=charts.realized_png(await heatmap.realized(c, w), f"{c} gerçekleşen likidasyonlar · {w}"), content_type="image/png")


async def card_png(req):
    import asyncio
    c = _coin(req)
    a, h, d = await asyncio.gather(analysis.analysis(c), heatmap.model(c, "24h"), orderbook.latest(c))
    return web.Response(body=charts.analysis_card_png(a, h, d), content_type="image/png")


async def card4k_png(req):
    return web.Response(body=await card4k.build(_coin(req)), content_type="image/png")

async def db_conns(req):
    return j({"self": db.APP_NAME, "connections": await db.connections()})

def app():
    a = web.Application(middlewares=[auth])
    a.add_routes([web.get("/", health), web.get("/health", health), web.get("/api/analysis", an), web.get("/api/report", live_report), web.get("/api/heatmap", heat),
                  web.get("/api/heatmap.png", heat_png), web.get("/api/liquidations", liq), web.get("/api/whales", whales), web.get("/api/whale-flow", whale_report),
                  web.get("/api/derivatives", deriv), web.get("/api/options", opts), web.get("/api/alts", alts), web.get("/api/cvd", cvd),
                  web.get("/panel", panel), web.get("/api/coins", coins), web.get("/api/depth", depth), web.get("/api/orderbook.png", depth_png),
                  web.get("/api/cvd.png", cvd_png), web.get("/api/liquidations.png", liq_png), web.get("/api/card.png", card_png),
                  web.get("/api/db-connections", db_conns), web.get("/api/card4k.png", card4k_png)])
    return a
