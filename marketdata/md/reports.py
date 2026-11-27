"""IMPERION Advanced Analysis Reports — live whale liquidity & exchange flow synthesis."""
import asyncio
from datetime import datetime, timezone
from . import analysis, heatmap, orderbook, whales, config, fmt

UTC = timezone.utc

async def generate_report(coin: str = "BTC", focus: str = "whale_liquidity") -> dict:
    """Generate a structured live analysis report focused on whale liquidity and exchange movements."""
    coin = coin.upper()
    if coin not in config.COINS:
        return {"error": f"{coin} not monitored"}

    # Parallel fetch of core live data
    a, hm, depth, whale_rep = await asyncio.gather(
        analysis.analysis(coin),
        heatmap.model(coin, "4h"),
        orderbook.latest(coin),
        whales.report(coin, "15m", limit=10),
        return_exceptions=True
    )

    report = {
        "coin": coin,
        "generated_utc": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
        "focus": focus,
        "price": a.get("price") if isinstance(a, dict) else None,
        "summary": [],
        "whale_liquidity": {},
        "exchange_flows": {},
        "risk_clusters": {},
        "disclaimer": "Educational research data only. Not investment advice. Heatmap is an estimated model."
    }

    if isinstance(a, dict):
        report["summary"] = a.get("summary", [])
        report["derivatives"] = {
            "oi_total_usd": a.get("derivatives", {}).get("oi_total_usd"),
            "funding_ann_pct": a.get("derivatives", {}).get("funding_ann_pct"),
            "oi_change_4h": a.get("derivatives", {}).get("oi_change_pct", {}).get("4h")
        }
        f4 = a.get("flows", {}).get("4h", {})
        report["exchange_flows"] = {
            "cvd_spot": f4.get("cvd", {}).get("spot", {}).get("delta"),
            "cvd_perp": f4.get("cvd", {}).get("perp", {}).get("delta"),
            "whales_net_4h": f4.get("whales", {}).get("net_usd"),
            "liquidations_4h": {
                "long": f4.get("liquidations", {}).get("long"),
                "short": f4.get("liquidations", {}).get("short")
            }
        }

    if isinstance(hm, dict):
        report["risk_clusters"] = {
            "nearest_long": next((c for c in hm.get("clusters", []) if c["side"] == "long"), None),
            "nearest_short": next((c for c in hm.get("clusters", []) if c["side"] == "short"), None),
            "venues_used": hm.get("venues_used")
        }

    if isinstance(whale_rep, dict):
        report["whale_liquidity"] = {
            "window": "15m",
            "net_usd": whale_rep.get("net_usd"),
            "buy_usd": whale_rep.get("buy_usd"),
            "sell_usd": whale_rep.get("sell_usd"),
            "recent_trades": whale_rep.get("trades", [])[:5]
        }

    if isinstance(depth, dict):
        b1 = depth.get("bands", {}).get("1", {})
        report["orderbook_imbalance"] = {
            "bid_usd": b1.get("bid_usd"),
            "ask_usd": b1.get("ask_usd"),
            "imbalance": b1.get("imbalance")
        }

    return report


def format_report_text(report: dict) -> str:
    """Turn structured report into Telegram-friendly text."""
    if "error" in report:
        return report["error"]
    lines = [
        f"<b>IMPERION LIVE REPORT · {report['coin']}</b>",
        f"{report['generated_utc']} UTC",
        "",
        f"Price: ${report.get('price', 0):,.0f}" if report.get("price") else "",
    ]
    lines += [f"• {s}" for s in report.get("summary", [])[:6]]
    wl = report.get("whale_liquidity", {})
    if wl:
        lines.append(f"\n<b>Whale Liquidity (15m)</b>")
        lines.append(f"Net {fmt.usd(wl.get('net_usd', 0))} · Buy {fmt.usd(wl.get('buy_usd', 0))} · Sell {fmt.usd(wl.get('sell_usd', 0))}")
    ef = report.get("exchange_flows", {})
    if ef:
        lines.append(f"\n<b>Exchange Flows (4h)</b>")
        lines.append(f"CVD Spot {fmt.usd(ef.get('cvd_spot', 0))} · Perp {fmt.usd(ef.get('cvd_perp', 0))}")
        lines.append(f"Whales net {fmt.usd(ef.get('whales_net_4h', 0))}")
    rc = report.get("risk_clusters", {})
    if rc.get("nearest_long") or rc.get("nearest_short"):
        lines.append("\n<b>Nearest Liquidation Clusters</b>")
        if rc.get("nearest_long"):
            c = rc["nearest_long"]
            lines.append(f"Long support ~${c['price']:,.0f} ({c['dist_pct']:+.1f}%)")
        if rc.get("nearest_short"):
            c = rc["nearest_short"]
            lines.append(f"Short resistance ~${c['price']:,.0f} ({c['dist_pct']:+.1f}%)")
    lines.append(f"\n<i>{report.get('disclaimer', '')}</i>")
    return "\n".join(lines)
