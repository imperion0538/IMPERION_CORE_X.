"""Processor: consumes NATS events, maintains state in Redis, computes heatmap / anomaly / cascade / options."""
import asyncio, json, logging, os
import nats
import redis.asyncio as redis
from shared.heatmap import build_enhanced_heatmap
from shared.anomaly import compute_cascade_risk
from .options_handler import handle_options_trade, handle_options_summary

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("processor")

NATS_URL = os.environ.get("NATS_URL")
REDIS_URL = os.environ.get("REDIS_URL")

async def handle_trade(r, msg):
    data = json.loads(msg.data)
    coin = data["coin"]
    await r.incrbyfloat(f"flow:{coin}:buy", data["price"] * data["qty"] if data["is_buy"] else 0)
    await r.incrbyfloat(f"flow:{coin}:sell", data["price"] * data["qty"] if not data["is_buy"] else 0)

async def periodic_analytics(r):
    while True:
        await asyncio.sleep(30)
        for coin in ["BTC", "ETH", "SOL"]:
            heatmap = build_enhanced_heatmap(
                oi_deltas=[{"price": 95000, "delta_usd": 50_000_000, "venue": "binance"}],
                current_price=96000,
                window_sec=86400
            )
            await r.set(f"heatmap:{coin}:24h", json.dumps(heatmap), ex=180)

            risk = compute_cascade_risk(
                heatmap_clusters=heatmap["clusters"],
                current_price=96000,
                funding_ann=12.5,
                cvd_4h=8_000_000,
                recent_liq_5m=1_200_000
            )
            await r.set(f"cascade:{coin}", json.dumps(risk), ex=60)

async def main():
    nc = await nats.connect(NATS_URL)
    r = await redis.from_url(REDIS_URL)
    log.info("Processor ready (with options)")

    trade_sub = await nc.subscribe("trade.>")
    opt_trade_sub = await nc.subscribe("options.trade.>")
    opt_sum_sub = await nc.subscribe("options.summary.>")
    asyncio.create_task(periodic_analytics(r))

    async def consume(sub, handler):
        async for msg in sub.messages:
            try:
                data = json.loads(msg.data)
                await handler(r, data)
            except Exception as e:
                log.exception("handler error: %s", e)

    await asyncio.gather(
        consume(trade_sub, lambda r, d: handle_trade(r, type("m", (), {"data": json.dumps(d).encode()})())),
        consume(opt_trade_sub, handle_options_trade),
        consume(opt_sum_sub, handle_options_summary),
    )

if __name__ == "__main__":
    asyncio.run(main())
