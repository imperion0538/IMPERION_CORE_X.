"""Deribit options collector: WS trades + REST summaries."""
import asyncio, json, logging
import aiohttp

log = logging.getLogger("deribit")

WS_URL = "wss://www.deribit.com/ws/api/v2"
REST = "https://www.deribit.com/api/v2"

async def deribit_options_trades(nc, publish):
    """Subscribe to all options trades for BTC/ETH."""
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(WS_URL) as ws:
            # Subscribe to options trades (currency-wide)
            for currency in ("BTC", "ETH"):
                msg = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "public/subscribe",
                    "params": {"channels": [f"trades.option.{currency}.100ms"]}
                }
                await ws.send_json(msg)
            log.info("Deribit options trades subscribed")

            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                data = json.loads(msg.data)
                if data.get("method") == "subscription":
                    params = data["params"]
                    channel = params["channel"]
                    if channel.startswith("trades.option."):
                        for trade in params["data"]:
                            coin = trade["instrument_name"].split("-")[0]
                            await publish(nc, f"options.trade.{coin}", trade)

async def deribit_options_summary(nc, publish):
    """Periodic REST poll for book summary (OI, volume, IV)."""
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                for currency in ("BTC", "ETH"):
                    url = f"{REST}/public/get_book_summary_by_currency"
                    params = {"currency": currency, "kind": "option"}
                    async with session.get(url, params=params) as resp:
                        result = (await resp.json()).get("result", [])
                        summary = {
                            "coin": currency,
                            "instruments": len(result),
                            "total_oi": sum(float(i.get("open_interest", 0)) for i in result),
                            "total_volume": sum(float(i.get("volume", 0)) for i in result),
                            "by_instrument": [
                                {
                                    "name": i["instrument_name"],
                                    "oi": float(i.get("open_interest", 0)),
                                    "volume": float(i.get("volume", 0)),
                                    "mark_iv": i.get("mark_iv"),
                                    "bid_iv": i.get("bid_iv"),
                                    "ask_iv": i.get("ask_iv")
                                }
                                for i in result if float(i.get("open_interest", 0)) > 0
                            ]
                        }
                        await publish(nc, f"options.summary.{currency}", summary)
                        log.info("Deribit %s options summary: OI=%.0f", currency, summary["total_oi"])
            except Exception as e:
                log.warning("Deribit summary error: %s", e)
            await asyncio.sleep(300)  # every 5 min
