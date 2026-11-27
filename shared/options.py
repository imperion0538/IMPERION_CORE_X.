"""Deribit options flow processing and analytics."""
from typing import Dict, List, Any
from collections import defaultdict

def parse_option_instrument(name: str) -> Dict:
    """Parse Deribit instrument name e.g. BTC-29MAR24-50000-C"""
    parts = name.split("-")
    if len(parts) < 4:
        return {}
    return {
        "coin": parts[0],
        "expiry": parts[1],
        "strike": float(parts[2]),
        "type": "call" if parts[3] == "C" else "put"
    }

def aggregate_options_flow(trades: List[Dict]) -> Dict[str, Any]:
    """Aggregate options trades into premium volume, put/call, large trades."""
    buy_premium = 0.0
    sell_premium = 0.0
    call_volume = 0.0
    put_volume = 0.0
    large_trades = []
    by_expiry = defaultdict(lambda: {"call_oi": 0, "put_oi": 0, "call_vol": 0, "put_vol": 0})

    for t in trades:
        parsed = parse_option_instrument(t.get("instrument_name", ""))
        if not parsed:
            continue
        amount = float(t.get("amount", 0))
        price = float(t.get("price", 0))  # in underlying or USD depending on currency
        # Deribit options price is in BTC/ETH for inverse; approximate USD
        index = float(t.get("index_price", 0))
        premium_usd = amount * price * index if index else amount * price

        direction = t.get("direction", "")
        if direction == "buy":
            buy_premium += premium_usd
        else:
            sell_premium += premium_usd

        if parsed["type"] == "call":
            call_volume += premium_usd
        else:
            put_volume += premium_usd

        if premium_usd >= 100_000:  # large options trade
            large_trades.append({
                "instrument": t["instrument_name"],
                "side": direction,
                "premium_usd": round(premium_usd, 0),
                "strike": parsed["strike"],
                "expiry": parsed["expiry"],
                "type": parsed["type"],
                "iv": t.get("iv")
            })

        by_expiry[parsed["expiry"]][f"{parsed['type']}_vol"] += premium_usd

    pc_ratio = put_volume / call_volume if call_volume else None
    return {
        "buy_premium_usd": round(buy_premium, 0),
        "sell_premium_usd": round(sell_premium, 0),
        "net_premium_usd": round(buy_premium - sell_premium, 0),
        "call_volume_usd": round(call_volume, 0),
        "put_volume_usd": round(put_volume, 0),
        "put_call_ratio": round(pc_ratio, 3) if pc_ratio else None,
        "large_trades": sorted(large_trades, key=lambda x: -x["premium_usd"])[:20],
        "by_expiry": dict(by_expiry)
    }

def calculate_max_pain(oi_by_strike: Dict[float, Dict]) -> float:
    """Simple max pain calculation from open interest by strike."""
    if not oi_by_strike:
        return 0.0
    strikes = sorted(oi_by_strike.keys())
    pain = {}
    for k in strikes:
        total = 0.0
        for strike, data in oi_by_strike.items():
            call_oi = data.get("call", 0)
            put_oi = data.get("put", 0)
            if strike > k:
                total += call_oi * (strike - k)
            if strike < k:
                total += put_oi * (k - strike)
        pain[k] = total
    return min(pain, key=pain.get) if pain else 0.0
