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

async def main():
    await db.init()
    runner = web.AppRunner(api.app()); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", config.PORT).start()
    log.info("API %s portunda; coinler %s", config.PORT, config.COINS)
    await asyncio.gather(*[supervise(f) for f in ws.ALL + rest.ALL + telegram.ALL])

if __name__ == "__main__":
    asyncio.run(main())
