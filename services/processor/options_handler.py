"""Processor handlers for Deribit options flow."""
import json, logging
from shared.options import aggregate_options_flow, calculate_max_pain, parse_option_instrument

log = logging.getLogger("options")

# In-memory buffer per coin (in production use Redis list/stream)
OPTIONS_BUFFER = {"BTC": [], "ETH": []}

async def handle_options_trade(r, data):
    coin = data.get("instrument_name", "").split("-")[0]
    if coin not in OPTIONS_BUFFER:
        return
    OPTIONS_BUFFER[coin].append(data)
    # Keep last 500 trades
    if len(OPTIONS_BUFFER[coin]) > 500:
        OPTIONS_BUFFER[coin] = OPTIONS_BUFFER[coin][-500:]

    # Recompute aggregate every 20 trades or on timer
    if len(OPTIONS_BUFFER[coin]) % 20 == 0:
        agg = aggregate_options_flow(OPTIONS_BUFFER[coin])
        await r.set(f"options:flow:{coin}", json.dumps(agg), ex=300)
        log.info("%s options net premium: %s", coin, agg["net_premium_usd"])

async def handle_options_summary(r, data):
    coin = data["coin"]
    # Build OI by strike for max pain
    oi_by_strike = {}
    for inst in data.get("by_instrument", []):
        parsed = parse_option_instrument(inst["name"])
        if not parsed:
            continue
        strike = parsed["strike"]
        if strike not in oi_by_strike:
            oi_by_strike[strike] = {"call": 0, "put": 0}
        oi_by_strike[strike][parsed["type"]] += inst["oi"]

    max_pain = calculate_max_pain(oi_by_strike)
    summary = {
        "coin": coin,
        "total_oi": data["total_oi"],
        "total_volume": data["total_volume"],
        "max_pain": max_pain,
        "instruments": data["instruments"],
        "top_oi": sorted(data["by_instrument"], key=lambda x: -x["oi"])[:15]
    }
    await r.set(f"options:summary:{coin}", json.dumps(summary), ex=600)
