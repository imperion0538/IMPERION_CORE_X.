"""Makro veri toplayıcı: DXY, 10Y yield, SPX, BTC ETF net flow. Gözlenen veriler; insufficient_data durumunda."""
import asyncio, json, logging, time
from datetime import datetime, timezone
from . import config, db, http

log = logging.getLogger("macro")
UTC = timezone.utc

async def yahoo_quote(symbol):
    """Yahoo Finance chart API'den son fiyat."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        data = await http.get(url, {"interval": "1d", "range": "5d"})
        result = data["chart"]["result"][0]
        meta = result["meta"]
        price = meta.get("regularMarketPrice") or meta.get("previousClose")
        return float(price) if price else None
    except Exception as e:
        log.warning("Yahoo %s hatası: %s", symbol, e)
        return None

async def fetch_macro():
    dxy, tnx, gspc = await asyncio.gather(
        yahoo_quote("DX-Y.NYB"),
        yahoo_quote("^TNX"),
        yahoo_quote("^GSPC"),
        return_exceptions=True
    )
    dxy = dxy if isinstance(dxy, (int, float)) else None
    tnx = tnx if isinstance(tnx, (int, float)) else None
    gspc = gspc if isinstance(gspc, (int, float)) else None

    from . import public_context
    etf = None
    try:
        feed = await asyncio.wait_for(public_context.etf("BTC"), timeout=12)
        if feed.get("status") in ("available", "partial"):
            etf = feed.get("latest_complete")
    except Exception as exc:
        log.warning("Farside ETF akışı alınamadı: %s", type(exc).__name__)
    status = "observed" if any(x is not None for x in (dxy, tnx, gspc)) else "insufficient_data"
    return {
        "dxy": dxy, "us10y": tnx, "spx": gspc, "btc_etf_net_flow_usd": etf["total_usd"] if etf else None,
        "btc_etf_flow_date": etf["date"] if etf else None,
        "status": status, "source": "Yahoo Finance",
        "fetched_utc": datetime.now(UTC).isoformat()
    }

async def poll_macro():
    while True:
        try:
            data = await fetch_macro()
            async with db.pool.acquire() as c:
                await c.execute(
                    "INSERT INTO macro_snap (ts, data) VALUES (date_trunc('hour', now()), $1::jsonb) ON CONFLICT (ts) DO UPDATE SET data = EXCLUDED.data",
                    json.dumps(data)
                )
            log.info("Makro güncellendi: DXY=%s 10Y=%s SPX=%s status=%s", data["dxy"], data["us10y"], data["spx"], data["status"])
        except Exception:
            log.exception("Makro poll hatası")
        await asyncio.sleep(3600)  # saatlik

async def latest_macro():
    async with db.pool.acquire() as c:
        row = await c.fetchrow("SELECT ts, data FROM macro_snap ORDER BY ts DESC LIMIT 1")
    if not row:
        return {"status": "insufficient_data"}
    data = json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]
    data["snapshot_utc"] = row["ts"].isoformat()
    age = (datetime.now(UTC) - row["ts"]).total_seconds()
    data["age_seconds"] = int(age)
    data["stale"] = age > 7200
    return data
