"""On-chain large transaction collector skeleton.

Supports Whale Alert (paid) or free public explorers / Bitquery.
Publishes to NATS subject: onchain.flow.{coin}
"""
import asyncio, os, logging
import aiohttp
import nats

log = logging.getLogger("onchain")
WHALE_ALERT_KEY = os.environ.get("WHALE_ALERT_API_KEY")
NATS_URL = os.environ.get("NATS_URL", "nats://nats:4222")

async def whale_alert_loop(nc):
    if not WHALE_ALERT_KEY:
        log.warning("WHALE_ALERT_API_KEY not set, on-chain disabled")
        return
    url = "https://api.whale-alert.io/v1/transactions"
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                params = {"api_key": WHALE_ALERT_KEY, "min_value": 500000, "limit": 50}
                async with session.get(url, params=params) as resp:
                    data = await resp.json()
                    for tx in data.get("transactions", []):
                        if tx.get("blockchain") in ("bitcoin", "ethereum"):
                            coin = "BTC" if tx["blockchain"] == "bitcoin" else "ETH"
                            await nc.publish(f"onchain.flow.{coin}", {
                                "usd": tx.get("amount_usd"),
                                "from": tx.get("from", {}).get("owner_type"),
                                "to": tx.get("to", {}).get("owner_type"),
                                "hash": tx.get("hash")
                            }.__str__().encode())  # simplify
            except Exception as e:
                log.exception("whale alert error: %s", e)
            await asyncio.sleep(60)

async def main():
    nc = await nats.connect(NATS_URL)
    await whale_alert_loop(nc)

if __name__ == "__main__":
    asyncio.run(main())
