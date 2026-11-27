"""HTTP API: Claude ve diğer istemciler için. /health dışındaki uçlar X-API-Key ister."""
import hmac, json, math, time
from datetime import timedelta
from aiohttp import web
from pathlib import Path
from . import analysis, card4k, charts, config, db, heatmap, orderbook, state, whales as whale_flow, reports
from . import intelligence, quality, livebook, ingest, context, outbox

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
    if req.path == "/api/db-connections" and not _key_ok(req):
        return web.json_response({"error": "unauthorized"}, status=401)
    if req.path in ("/", "/health", "/ready", "/panel") or _key_ok(req):
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
    return j({"status": "ok", "version": "2.0", "data_status": data_status, "ingest_queue": ingest.QUEUE.qsize(),
              "ingestion": dict(quality.counters), "stale_streams": stale, "streams": {n: {"age_s": int(now - h["last_msg"]), "reconnects": h["reconnects"], "msgs": h["msgs"]} for n, h in state.stream_health.items()}})


async def ready(req):
    try:
        writer = await db.is_writer()
    except Exception:
        writer = False
    last = quality.counters.get("last_commit_epoch", 0)
    ok = writer and time.time()-last < 15 and ingest.QUEUE.qsize() < 10000
    return web.json_response({"ready": ok, "database_writer": writer, "queue_size": ingest.QUEUE.qsize()}, status=200 if ok else 503)


async def intelligence_report(req):
    coin = _coin(req)
    if coin not in config.COINS:
        return web.json_response({"error": "Coin is not monitored"}, status=400)
    return j(await intelligence.generate(coin))


async def data_quality(req):
    coin = _coin(req)
    if coin not in config.COINS:
        return web.json_response({"error": "Coin is not monitored"}, status=400)
    return j({"trades": quality.snapshot(coin), "orderbooks": livebook.snapshot(coin),
              "queue_size": ingest.QUEUE.qsize(), "notifications": await outbox.status()})


async def live(req):
    coin = _coin(req)
    if coin not in config.COINS:
        return web.json_response({"error": "Coin is not monitored"}, status=400)
    return j({"coin": coin, "as_of_epoch": time.time(), "price": intelligence.consensus(coin),
              "quality": quality.snapshot(coin), "orderbooks": livebook.snapshot(coin)})


async def forward_evidence(req):
    from . import archive_store, http
    coin = _coin(req)
    if coin not in config.COINS:
        return web.json_response({"error": "Coin is not monitored"}, status=400)
    return j(await http.cached("forward-evidence:"+coin, 60, lambda: archive_store.evaluation(coin)))


async def research_history(req):
    coin = _coin(req)
    if coin not in config.COINS:
        return web.json_response({"error": "Coin is not monitored"}, status=400)
    async with db.pool.acquire() as conn:
        rows = await conn.fetch("SELECT ts,data FROM research_snapshots WHERE coin=$1 ORDER BY ts DESC LIMIT 12", coin)
    return j([{"ts": r["ts"], "report": json.loads(r["data"])} for r in rows])


async def risk(req):
    try:
        args = {k: float(req.query[k]) for k in ("equity", "risk_pct", "entry", "stop")}
        args.update({k: float(req.query[k]) for k in ("fee_bps", "slippage_bps") if k in req.query})
        return j(context.risk_budget(**args))
    except (ValueError, KeyError):
        return web.json_response({"error": "Use finite positive equity, risk_pct (0..100], entry and a different stop; optional fee_bps/slippage_bps"}, status=400)


async def replay_check(req):
    from . import replay
    if _coin(req) not in config.COINS:
        return web.json_response({"error": "Coin is not monitored"}, status=400)
    return j(await replay.check(_coin(req)))

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


async def research_png(req):
    from . import research_card
    if _coin(req) not in config.COINS:
        return web.json_response({"error": "Coin is not monitored"}, status=400)
    return web.Response(body=await research_card.build(_coin(req)), content_type="image/png")


async def card4k_png(req):
    return web.Response(body=await card4k.build(_coin(req)), content_type="image/png")

async def db_conns(req):
    return j({"self": db.APP_NAME, "connections": await db.connections()})

def app():
    a = web.Application(middlewares=[auth])
    a.add_routes([web.get("/", health), web.get("/health", health), web.get("/api/analysis", an), web.get("/api/report", live_report), web.get("/api/heatmap", heat),
                  web.get("/ready", ready), web.get("/api/intelligence", intelligence_report), web.get("/api/quality", data_quality),
                  web.get("/api/live", live),
                  web.get("/api/replay-check", replay_check),
                  web.get("/api/research4k.png", research_png), web.get("/api/forward-evidence", forward_evidence), web.get("/api/research-history", research_history), web.get("/api/risk", risk),
                  web.get("/api/heatmap.png", heat_png), web.get("/api/liquidations", liq), web.get("/api/whales", whales), web.get("/api/whale-flow", whale_report),
                  web.get("/api/derivatives", deriv), web.get("/api/options", opts), web.get("/api/alts", alts), web.get("/api/cvd", cvd),
                  web.get("/panel", panel), web.get("/api/coins", coins), web.get("/api/depth", depth), web.get("/api/orderbook.png", depth_png),
                  web.get("/api/cvd.png", cvd_png), web.get("/api/liquidations.png", liq_png), web.get("/api/card.png", card_png),
                  web.get("/api/db-connections", db_conns), web.get("/api/card4k.png", card4k_png)])
    return a
