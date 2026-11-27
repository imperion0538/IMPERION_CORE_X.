"""HTTP API: Claude ve diğer istemciler için. /health dışındaki uçlar X-API-Key ister."""
import hmac, json, time
from datetime import timedelta
from aiohttp import web
from . import analysis, charts, config, db, heatmap, state

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
    if req.path in ("/", "/health") or _key_ok(req):
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
    return j({"status": "ok", "streams": {n: {"age_s": int(now - h["last_msg"]), "reconnects": h["reconnects"], "msgs": h["msgs"]} for n, h in state.stream_health.items()}})

async def an(req): return j(await analysis.analysis(_coin(req)))
async def heat(req):
    w = _win(req)
    if w not in heatmap.WINDOWS: return j({"error": f"window şunlardan biri olmalı: {list(heatmap.WINDOWS)}"})
    return j(await heatmap.model(_coin(req), w))
async def heat_png(req):
    c, w = _coin(req), _win(req); h = await heatmap.model(c, w)
    return web.Response(body=charts.heatmap_png(h, f"{c} tahmini likidasyon haritası · {w}"), content_type="image/png")
async def liq(req): return j(await heatmap.realized(_coin(req), _win(req)))
async def whales(req):
    secs = {"1h": 3600, "4h": 14400, "24h": 86400, "7d": 604800}.get(_win(req, "4h"), 14400)
    async with db.pool.acquire() as c:
        rows = await c.fetch("SELECT ts, venue, market, side, price, usd FROM whale_trades WHERE coin=$1 AND ts > now() - $2::interval AND usd >= $3 ORDER BY ts DESC LIMIT 500",
                             _coin(req), timedelta(seconds=secs), float(req.query.get("min_usd", 0)))
    return j([dict(r) for r in rows])
async def deriv(req): return j(await analysis.deriv(_coin(req)))
async def opts(req): return j(await analysis.options(_coin(req)))
async def alts(req): return j(await analysis.alts(req.query.get("base", "BTC").upper()))
async def cvd(req):
    secs = {"1h": 3600, "4h": 14400, "24h": 86400, "7d": 604800}.get(_win(req), 86400)
    async with db.pool.acquire() as c:
        rows = await c.fetch("SELECT date_trunc('hour', ts) h, market, sum(buy_usd) b, sum(sell_usd) s, avg(close) px FROM trades_1m WHERE coin=$1 AND ts > now() - $2::interval GROUP BY 1, 2 ORDER BY 1",
                             _coin(req), timedelta(seconds=secs))
    return j([{"hour": r["h"], "market": r["market"], "buy_usd": r["b"], "sell_usd": r["s"], "delta": r["b"] - r["s"], "avg_price": r["px"]} for r in rows])

def app():
    a = web.Application(middlewares=[auth])
    a.add_routes([web.get("/", health), web.get("/health", health), web.get("/api/analysis", an), web.get("/api/heatmap", heat),
                  web.get("/api/heatmap.png", heat_png), web.get("/api/liquidations", liq), web.get("/api/whales", whales),
                  web.get("/api/derivatives", deriv), web.get("/api/options", opts), web.get("/api/alts", alts), web.get("/api/cvd", cvd)])
    return a
