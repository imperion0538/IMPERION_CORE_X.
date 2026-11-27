"""IMPERION Market Data: collectors, Telegram bot, API, and live research reports."""
import asyncio, logging, sys
from aiohttp import web
from md import api, config, db, orderbook, rest, telegram, ws, reports

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("main")

async def supervise(fn):
    while True:
        try:
            await fn()
            if fn in telegram.ALL: return
        except Exception:
            log.exception("%s crashed, restarting in 10s", fn.__name__)
        await asyncio.sleep(10)

async def selftest():
    from md import analysis, heatmap
    await asyncio.sleep(90)
    for w in heatmap.WINDOWS:
        try:
            h = await heatmap.model("BTC", w)
            log.info("SELFTEST heatmap %s price=%.0f venues=%s clusters=%s", w, h["price"], h["venues_used"],
                     [(c["side"], round(c["price"]), round(c["usd"] / 1e6, 1)) for c in h["clusters"]])
        except Exception:
            log.exception("SELFTEST heatmap %s error", w)
    try:
        a = await analysis.alts("BTC")
        log.info("SELFTEST alts %s", [(x["coin"], round(x["corr"], 2), round(x["beta"], 2)) for x in a[:6]])
    except Exception:
        log.exception("SELFTEST alts error")
    await asyncio.sleep(330)
    try:
        a = await analysis.analysis("BTC")
        log.info("SELFTEST analysis %s", " | ".join(a["summary"]))
        r = await reports.generate_report("BTC")
        log.info("SELFTEST report summary length=%d", len(r.get("summary", [])))
    except Exception:
        log.exception("SELFTEST analysis/report error")

async def writer_watchdog():
    import os
    while True:
        await asyncio.sleep(30)
        try:
            if not await db.is_writer():
                log.warning("no longer the active writer; exiting")
                os._exit(0)
        except Exception as e:
            log.warning("writer check failed: %s", e)

async def main():
    await db.init()
    async with db.pool.acquire() as c:
        first = await c.fetchval("SELECT extract(epoch FROM min(ts)) FROM trades_1m")
    if first: config.STARTED_AT = float(first)
    runner = web.AppRunner(api.app()); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", config.PORT).start()
    log.info("API on port %s; coins %s", config.PORT, config.COINS)
    asyncio.create_task(selftest())
    asyncio.create_task(writer_watchdog())
    await asyncio.gather(*[supervise(f) for f in ws.ALL + rest.ALL + [orderbook.poll] + telegram.ALL])

if __name__ == "__main__":
    asyncio.run(main())
