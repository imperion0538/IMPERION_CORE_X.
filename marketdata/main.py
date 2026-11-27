"""IMPERION Market Data: toplayıcıları, Telegram botunu ve API'yi başlatır."""
import asyncio, logging
from aiohttp import web
from md import api, config, db, rest, telegram, ws

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("main")

async def supervise(fn):
    while True:
        try:
            await fn()
            if fn in telegram.ALL: return  # yapılandırma yoksa sessizce biter
        except Exception:
            log.exception("%s çöktü, 10 sn sonra yeniden başlıyor", fn.__name__)
        await asyncio.sleep(10)

async def selftest():
    """Açılış testi: modelleri çalıştırıp özetini loglar (anahtar gerektirmeden doğrulama için)."""
    from md import analysis, heatmap
    await asyncio.sleep(90)
    for w in heatmap.WINDOWS:
        try:
            h = await heatmap.model("BTC", w)
            log.info("SELFTEST heatmap %s fiyat=%.0f borsalar=%s kümeler=%s", w, h["price"], h["venues_used"],
                     [(c["side"], round(c["price"]), round(c["usd"] / 1e6, 1)) for c in h["clusters"]])
        except Exception:
            log.exception("SELFTEST heatmap %s hata", w)
    try:
        a = await analysis.alts("BTC"); log.info("SELFTEST alts %s", [(x["coin"], round(x["corr"], 2), round(x["beta"], 2)) for x in a[:6]])
    except Exception:
        log.exception("SELFTEST alts hata")
    await asyncio.sleep(330)
    try:
        a = await analysis.analysis("BTC")
        log.info("SELFTEST analysis %s", " | ".join(a["summary"]))
        from md import fmt
        log.info("SELFTEST text uzunluk=%d", len(fmt.analysis_text(a)))
    except Exception:
        log.exception("SELFTEST analysis hata")

async def main():
    await db.init()
    runner = web.AppRunner(api.app()); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", config.PORT).start()
    log.info("API %s portunda; coinler %s", config.PORT, config.COINS)
    asyncio.create_task(selftest())
    await asyncio.gather(*[supervise(f) for f in ws.ALL + rest.ALL + telegram.ALL])

if __name__ == "__main__":
    asyncio.run(main())
