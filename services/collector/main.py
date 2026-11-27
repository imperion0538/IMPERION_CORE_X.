"""Collector service: publishes trades, liquidations, OI snapshots and Deribit options to NATS."""
import asyncio, json, logging, os
import aiohttp
import nats
from .deribit import deribit_options_trades, deribit_options_summary

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("collector")

NATS_URL = os.environ.get("NATS_URL", "nats://nats:4222")
COINS = [c.strip().upper() for c in os.environ.get("COINS", "BTC,ETH,SOL").split(",")]

async def publish(nc, subject, payload):
    await nc.publish(subject, json.dumps(payload, default=str).encode())

async def binance_perp_trades(nc):
    streams = "/".join(f"{c.lower()}usdt@aggTrade" for c in COINS)
    url = f"wss://fstream.binance.com/stream?streams={streams}"
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            log.info("Binance perp trades connected")
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    d = json.loads(msg.data).get("data", {})
                    if d.get("e") == "aggTrade":
                        coin = d["s"][:-4]
                        await publish(nc, f"trade.{coin}.binance.perp", {
                            "ts": d["T"], "coin": coin, "venue": "binance", "market": "perp",
                            "price": float(d["p"]), "qty": float(d["q"]), "is_buy": not d["m"]
                        })

async def main():
    nc = await nats.connect(NATS_URL)
    log.info("NATS connected")
    await asyncio.gather(
        binance_perp_trades(nc),
        deribit_options_trades(nc, publish),
        deribit_options_summary(nc, publish),
    )

if __name__ == "__main__":
    asyncio.run(main())
